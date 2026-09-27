# -*- coding: utf-8 -*-
"""网页抓取与正文清洗。

**这条纪律很重要（也是防幻觉的关键）**：
    没有真正抓取成功的 URL，不允许出现在给学生的资料清单里。
    模型天然会"顺手编一个看起来很合理的链接"，所以引用前必须过一遍 fetch。

同时做了抓取礼节：超时、限速、只取正文前 N 字符、不递归爬站。
"""
import re
import time
import urllib.request
from typing import Any, Dict

UA = "Mozilla/5.0 (compatible; EduAgent/0.1; +mailto:17737056853@163.com)"
_last_fetch = [0.0]
MIN_INTERVAL = 1.0          # 同一进程内两次抓取的最小间隔（秒）

# 正文容器候选（按命中概率排序，命中即用）
CONTENT_HINTS = ("<article", "<main", 'class="markdown', 'id="content"',
                 'class="post', '<div class="entry')


def _clean_html(html: str) -> str:
    html = re.sub(r"<(script|style|noscript|svg|head|nav|footer|form)\b.*?</\1>", " ",
                  html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|li|h[1-6]|tr|pre|section|article|blockquote)>", "\n", html, flags=re.I)
    html = re.sub(r"<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", html, flags=re.I)
    html = re.sub(r"<li[^>]*>", "- ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    import html as _h
    txt = _h.unescape(html)
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return "\n".join(ln.strip() for ln in txt.split("\n") if ln.strip())


def fetch_text(url: str, max_chars: int = 4000, timeout: int = 25) -> Dict[str, Any]:
    """抓取 URL 并清洗出正文。返回 ok=False 时调用方必须把这条来源标为"未验证"。"""
    gap = time.time() - _last_fetch[0]
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    out: Dict[str, Any] = {"url": url, "ok": False, "title": "", "text": "", "chars": 0}
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000)
            ctype = (r.headers.get("Content-Type") or "").lower()
        _last_fetch[0] = time.time()
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            out.update(ok=True, title=url.rsplit("/", 1)[-1],
                       text="[PDF 文档，正文抽取需另接解析器（如 MinerU / PyMuPDF）]",
                       chars=0, content_type=ctype)
            return out
        html = raw.decode("utf-8", "replace")
        if "charset=gb" in ctype or "charset=gb" in html[:2000].lower():
            html = raw.decode("gbk", "replace")
        t = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = _clean_html(t.group(1)) if t else ""
        body = html
        for hint in CONTENT_HINTS:                     # 尽量只留正文区
            i = html.lower().find(hint.lower())
            if i > 0:
                body = html[i:i + 400_000]
                break
        text = _clean_html(body)[:max_chars]
        out.update(ok=bool(text), title=title[:200], text=text, chars=len(text),
                   content_type=ctype)
    except Exception as exc:
        out["error"] = repr(exc)[:200]
    return out
