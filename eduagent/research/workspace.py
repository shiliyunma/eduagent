# -*- coding: utf-8 -*-
"""调研工作区：工具之间交换中间结果的地方。

**这是一个刻意的设计决定（论文里可写）**：
    检索一次可能返回几十条带摘要的条目，如果让模型把这些 JSON 在工具之间搬来搬去，
    上下文会被瞬间撑爆，而且模型抄写长 JSON 极易出错（截断、改字段）。
    所以中间结果落在**按会话隔离的工作区**里，工具之间只传 topic 这个 key，
    模型只需要看"摘要视图"。

    这也是本课题把"上下文预算"当第一等公民的一个具体体现。
"""
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from .. import config, ctx

WS_DIR = config.RUN_DIR / "research"


def _path(topic: str) -> Path:
    WS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", topic)[:60] or "topic"
    return WS_DIR / ("%s__%s.json" % (ctx.current_session()[:12] or "anon", safe))


def load(topic: str) -> Dict[str, Any]:
    p = _path(topic)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"topic": topic, "created_at": time.time(), "candidates": [],
            "map": None, "route": None, "notes": []}


def save(topic: str, data: Dict[str, Any]) -> str:
    p = _path(topic)
    data["updated_at"] = time.time()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(p)


def add_candidates(topic: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    d = load(topic)
    seen = {c.get("url") for c in d["candidates"]}
    added = 0
    for it in items:
        if it.get("url") and it["url"] not in seen:
            d["candidates"].append(it)
            seen.add(it["url"])
            added += 1
    save(topic, d)
    return {"added": added, "total": len(d["candidates"])}


def mark_fetched(topic: str, url: str, ok: bool, chars: int = 0, text: str = "") -> None:
    d = load(topic)
    for c in d["candidates"]:
        if c.get("url") == url:
            c["fetched"] = bool(ok)
            c["fetched_chars"] = chars
            if text:
                c["excerpt"] = text[:1200]
            break
    save(topic, d)


def verified(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """只返回"抓取成功"或"本来就是论文条目"的来源。

    这条纪律直接对应评测指标里的"来源可验证率 / 编造率"：
    没验证过的 URL 不允许进入最终资料清单。
    """
    out = []
    for c in d.get("candidates", []):
        if c.get("fetched") or c.get("source_type") in ("paper", "preprint"):
            out.append(c)
    return out
