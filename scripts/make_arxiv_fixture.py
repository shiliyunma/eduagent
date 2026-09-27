# -*- coding: utf-8 -*-
"""抓取一次真实 arXiv 响应并存成离线样例（data/fixtures_arxiv.jsonl）。

只在网络可用且没被限速时跑一次；之后离线模式、单元测试、答辩现场都靠这份存档。
用法：python scripts/make_arxiv_fixture.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eduagent.research import arxiv   # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "fixtures_arxiv.jsonl"
QUERIES = ["educational agent", "tool learning", "retrieval augmented generation"]

def main() -> int:
    all_items = []
    seen = set()
    fellback = True
    for q in QUERIES:
        items = []
        for attempt in range(3):
            try:
                items = arxiv.search(q, limit=8, use_cache=False)
                print("OK  %-32s %d 条" % (q, len(items)))
                break
            except Exception as exc:
                print("重试 %-28s %r" % (q, repr(exc)[:90]))
                items = []
                time.sleep(25)
        if any(not it.get("fixture_note") for it in items):
            fellback = False          # 至少有一批是真·在线数据
        for it in items:
            if it["url"] not in seen:
                all_items.append(it)
                seen.add(it["url"])
        time.sleep(4)

    if not all_items:
        print("没有取到任何数据（可能仍在被限速），稍后再试。")
        return 1
    if fellback:
        print("⚠ 全部来自离线样例（在线检索仍被限速），**不覆盖** %s —— 现有样例保持不动。" % OUT)
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for it in all_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print("已写入 %s：%d 条" % (OUT, len(all_items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
