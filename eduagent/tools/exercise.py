# -*- coding: utf-8 -*-
"""习题检索工具：接 RAGLearn 的 MySQL exercise_index 表（约 2400 条）。

生产路径（USE_RAGLEARN=1 且有 MySQL）走 BM25 + LIKE；
默认走本地样例文件，保证零依赖可跑。
"""
import json
from pathlib import Path
from typing import Any, Dict, List

from .. import config
from ..registry import register

_SAMPLE_PATH = config.DATA_DIR / "sample_exercise.jsonl"


def _load_sample() -> List[Dict[str, Any]]:
    if not _SAMPLE_PATH.exists():
        return []
    return [json.loads(l) for l in _SAMPLE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _search_mysql(keyword: str, subject: str = None, limit: int = 3):
    """接 RAGLearn 的 exercise_index（省略：见 E:\\RAGLearn\\scripts\\build_exercise_index.py）。"""
    import importlib
    import sys
    sys.path.insert(0, str(config.RAGLEARN_HOME))
    scripts = importlib.import_module("config")           # RAGLearn 的 config 里有 MySQL 连接信息
    import pymysql                                        # noqa: F401  需要时再装
    ...


def _check_ex() -> bool:
    return _SAMPLE_PATH.exists()


@register(
    name="search_exercise",
    description=("从课程习题库检索练习题（按关键词/章节），返回题干、答案与解析摘要。"
                 "学生要练习、要真题、要某章题目时用它。"),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "关键词，如 快速排序 / B+树 / TCP 三次握手"},
            "subject": {"type": "string", "description": "限定科目，可不传"},
            "limit": {"type": "integer", "description": "返回条数，默认 3", "default": 3},
        },
        "required": ["keyword"],
    },
    toolset="core",
    check_fn=_check_ex,
)
def search_exercise(keyword: str, subject: str = None, limit: int = 3) -> str:
    if config.USE_RAGLEARN:
        try:
            return _search_mysql(keyword, subject, limit)
        except Exception:
            pass                                # 落到本地样例
    rows = _load_sample()
    from .kbsearch import _query_tokens          # 复用中文二元组切词（见 kbsearch 的说明）
    toks = _query_tokens(keyword)
    scored = []
    for r in rows:
        text = str(r.get("text", "")) + str(r.get("solution", "")) + str(r.get("chapter", ""))
        score = sum(text.count(t) * min(len(t), 4) for t in toks)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    hit = [r for _, r in scored]
    if subject:
        hit = [r for r in hit if subject in r.get("subject", "")] or hit
    if not hit:
        return "习题库里没有匹配「%s」的题目。" % keyword
    out = []
    for i, r in enumerate(hit[: int(limit)], 1):
        out.append("题目 %d（%s %s）\n题干：%s\n答案：%s\n解析：%s" % (
            i, r.get("subject", ""), r.get("chapter", ""),
            r.get("text", ""), r.get("answer", ""), r.get("solution", "")))
    return "\n\n".join(out)
