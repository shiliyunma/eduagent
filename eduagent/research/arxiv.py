# -*- coding: utf-8 -*-
"""arXiv 检索（官方 API，免费、无需 key、可复现）。

三个工程细节（都是踩出来的，论文"实现"一节可以写）：
  1. **必须显式带 `Accept` 头**：arXiv 的 API 在没有 Accept 时会返回 406 Not Acceptable。
     curl 默认发 `Accept: */*` 所以能通，urllib 默认不发 —— 这个坑很容易卡住半天。
  2. **必须限速**：官方要求"不要快于每 3 秒一次请求"，连续快速请求会被 406/503 拦掉一段时间。
     所以这里做了全局最小间隔 + 指数退避重试。
  3. **必须缓存**：同一主题反复检索既慢又会触发限速；结果按 (query, limit, since) 缓存到本地。
     缓存也让实验可复现（论文里"同一查询多次运行结果一致"）。
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}
UA = "EduAgent/0.1 (undergraduate thesis; mailto:17737056853@163.com)"

MIN_INTERVAL = 3.0          # 官方要求的礼貌间隔（秒）
CACHE_TTL = 6 * 3600        # 缓存有效期 6 小时
_last_call = [0.0]
_OFFLINE = os.getenv("EDUAGENT_OFFLINE", "0") == "1"

FIXTURE = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures_arxiv.jsonl"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "run" / "cache"


def _cache_path(query: str, limit: int, since: Optional[str]) -> Path:
    key = hashlib.md5(("%s|%d|%s" % (query, limit, since)).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / ("arxiv_%s.json" % key)


def _get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _throttle() -> None:
    gap = time.time() - _last_call[0]
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_call[0] = time.time()


def _fixture_items() -> List[Dict[str, Any]]:
    """离线样例（无网络/被限速时用），来自真实 API 响应的存档。"""
    if not FIXTURE.exists():
        return []
    out = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def search(query: str, limit: int = 8, since: Optional[str] = None,
           sort: str = "relevance", use_cache: bool = True) -> List[Dict[str, Any]]:
    """检索 arXiv。since 形如 '2024-01-01'，用于研究方向的时间窗过滤。"""
    cp = _cache_path(query, limit, since)
    if use_cache and cp.exists() and (time.time() - cp.stat().st_mtime) < CACHE_TTL:
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            if data:
                return data[:limit]
        except json.JSONDecodeError:
            pass

    if _OFFLINE:
        all_fx = _fixture_items()
        if not all_fx:
            raise RuntimeError("离线模式且没有样例数据（data/fixtures_arxiv.jsonl）")
        return _rank(all_fx, query)[:limit]

    q = ('all:"%s"' % query.replace('"', " ")) if " " in query.strip() else "all:" + query.strip()
    params = {"search_query": q, "start": 0,
              "max_results": max(1, min(int(limit) * 2, 40))}
    if sort == "recent":
        params.update({"sortBy": "submittedDate", "sortOrder": "descending"})
    url = API + "?" + urllib.parse.urlencode(params)

    last_exc: Optional[Exception] = None
    for attempt, wait in enumerate((0, 5, 15, 40)):
        if wait:
            time.sleep(wait)                       # 被限速时越等越久
        _throttle()
        try:
            items = _parse(_get(url), limit, since)
            if items:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            return items
        except Exception as exc:                   # 406/503 都是"被限速"的常见表现
            last_exc = exc
            continue
    # 全部失败 → 落回离线样例，保证链路不崩（这也是答辩现场的保险）
    fx = _filter(_fixture_items(), since)[:limit]
    if fx:
        print("[arxiv] 在线检索失败（%r），已落回离线样例数据" % (last_exc,))
        return fx
    raise RuntimeError("arXiv 检索失败：%r" % (last_exc,))


def _filter(items: List[Dict[str, Any]], since: Optional[str]) -> List[Dict[str, Any]]:
    if not since:
        return items
    return [x for x in items if (x.get("published") or "") >= since]


def _rank(items: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """离线模式下的相关性排序：查询词命中则排前，全不命中则原样返回。

    （离线样例是人工整理的通用清单，不按主题切分；这里只做轻量排序，
    不假装它是一次真实检索 —— 演示脚本会打印'离线样例'字样。）
    """
    toks = [t for t in query.lower().replace("/", " ").split() if len(t) > 1]

    def score(x):
        blob = ((x.get("title") or "") + " " + (x.get("summary") or "")).lower()
        return -sum(blob.count(t) for t in toks)

    return sorted(items, key=score)


def _parse(xml: str, limit: int, since: Optional[str]) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml)
    out: List[Dict[str, Any]] = []
    for e in root.findall("a:entry", NS):
        title = " ".join((e.findtext("a:title", "", NS) or "").split())
        summary = " ".join((e.findtext("a:summary", "", NS) or "").split())
        published = (e.findtext("a:published", "", NS) or "")[:10]
        if since and published and published < since:
            continue
        link = ""
        for l in e.findall("a:link", NS):
            if l.get("rel") == "alternate":
                link = l.get("href") or ""
        aid = (e.findtext("a:id", "", NS) or "").split("/abs/")[-1]
        authors = [a.findtext("a:name", "", NS) for a in e.findall("a:author", NS)]
        out.append({
            "title": title,
            "url": link or ("https://arxiv.org/abs/" + aid),
            "arxiv_id": re.sub(r"v\d+$", "", aid),
            "summary": summary[:600],
            "published": published,
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "venue": "arXiv",
            "authors": authors[:4],
            "lang": "en",
            "source_type": "paper",
        })
        if len(out) >= int(limit):
            break
    return out
