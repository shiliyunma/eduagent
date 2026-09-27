# -*- coding: utf-8 -*-
"""课程知识检索工具：接 RAGLearn 的检索底座（毕设里唯一的"重"依赖）。

两种模式：
  USE_RAGLEARN=0（默认）→ 读 data/sample_kb.jsonl，零依赖，所有人都能跑
  USE_RAGLEARN=1        → 调 RAGLearn 的 multi_retrieve()，走真实的
                          ChromaDB + bge 嵌入 + Cross-Encoder 重排流水线

设计要点（论文"系统设计"一节可以写）：
- 工具只暴露"查课程知识"这一个语义，不暴露 RAGLearn 的内部参数
  （改检索参数不该改工具接口 —— 这是分层的意义）。
- 返回值**强制带出处**：教学场景下没有出处的回答不可用。
- 检索不到就返回"资料里没有"，不编造 —— 这是防幻觉的第一道闸。
"""
import json
from pathlib import Path
from typing import Any, Dict, List

from .. import config
from ..registry import register

_SAMPLE_PATH = config.DATA_DIR / "sample_kb.jsonl"


# ---------------------------------------------------------------- 数据源
def _load_sample() -> List[Dict[str, Any]]:
    if not _SAMPLE_PATH.exists():
        return []
    rows = []
    for line in _SAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _query_tokens(query: str):
    """把查询切成 token：空白分词 + 中文二元组。

    中文没有空格，直接按整句匹配会一个都搜不到 —— 这是很常见的坑，
    所以这里对每个中文片段再切出二元组（"快速排序" → 快速/速排/排序）。
    """
    import re
    toks = [t for t in re.split(r"\s+", str(query)) if t]
    for seg in re.findall(r"[\u4e00-\u9fff]+", str(query)):
        for i in range(len(seg) - 1):
            toks.append(seg[i:i + 2])
    return set(toks)


def _search_sample(query: str, subject: str = None, top_k: int = None) -> List[Dict[str, Any]]:
    """样例检索：关键词打分（只为把链路跑通，不是本毕设的贡献点）。

    正式版应换成向量检索 —— 就是接 RAGLearn 的那条路。
    """
    top_k = top_k or config.KB_TOP_K
    tokens = _query_tokens(query)
    if not tokens:
        return []
    scored = []
    for row in _load_sample():
        if subject and subject not in str(row.get("subject", "")):
            continue
        text = " ".join([str(row.get("text", "")), str(row.get("section", "")),
                         str(row.get("chapter", ""))])
        score = sum(text.count(t) * min(len(t), 4) for t in tokens)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:top_k]]


def _search_raglearn(query: str, subject: str = None, top_k: int = None) -> List[Dict[str, Any]]:
    """接真实检索底座：动态 import，避免没配 RAGLearn 时直接崩。"""
    import importlib
    import sys
    home = str(config.RAGLEARN_HOME)
    if home not in sys.path:
        sys.path.insert(0, home)
    query_mod = importlib.import_module("query")          # E:\RAGLearn\query.py
    docs, _embed_ms, _retrieval_ms, _queries = query_mod.multi_retrieve(query)
    out = []
    for d in docs[: (top_k or config.KB_TOP_K)]:
        meta = getattr(d, "metadata", {}) or {}
        out.append({
            "text": getattr(d, "page_content", str(d)),
            "subject": meta.get("subject", ""),
            "chapter": meta.get("chapter_title") or meta.get("chapter", ""),
            "section": meta.get("section_title") or meta.get("section", ""),
            "source": meta.get("source", "RAGLearn"),
        })
    return out


def _check_kb() -> bool:
    """门控：两个数据源至少有一个可用，工具才会出现在工具清单里。"""
    if _SAMPLE_PATH.exists():
        return True
    return Path(config.RAGLEARN_HOME).exists()


# ---------------------------------------------------------------- 工具
@register(
    name="search_course_kb",
    description=("检索课程知识库，返回与问题相关的课程资料片段（带【出处】）。"
                 "回答任何具体知识点问题之前都应该先调用它。"),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词或问题，越具体越好"},
            "subject": {"type": "string", "description": "限定科目，如 数据结构 / 计算机网络；不确定就不传"},
        },
        "required": ["query"],
    },
    toolset="core",
    check_fn=_check_kb,
)
def search_course_kb(query: str, subject: str = None) -> str:
    try:
        rows = _search_raglearn(query, subject) if config.USE_RAGLEARN else _search_sample(query, subject)
    except Exception as exc:                       # 检索挂了不能拖垮整轮对话
        return "检索失败：%r。可以换个说法重试，或直接提示学生稍后再问。" % (exc,)
    if not rows:
        return "课程资料里没有找到与「%s」相关的内容。" % query
    blocks = []
    for i, r in enumerate(rows, 1):
        src = " · ".join([p for p in (r.get("subject"), r.get("chapter"), r.get("section")) if p])
        blocks.append("[%d] 【出处】%s\n%s" % (i, src or r.get("source", "未知来源"), r.get("text", "")))
    return "\n\n".join(blocks)
