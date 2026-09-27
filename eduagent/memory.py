# -*- coding: utf-8 -*-
"""记忆：MEMORY.md 快照 + 学情画像。

关键设计（这一条是照 Hermes 学的，也是论文里能讲的一个点）：
记忆在**会话开始时读一次，快照进系统提示词**，之后不再改当前会话的提示词。
理由：系统提示词是前缀缓存的一部分，中途变更会让缓存全部失效；
     而且"记忆新鲜 5 秒"的价值远低于"缓存命中省下的钱和首字延迟"。
新写入的记忆从**下一个会话**开始生效。
"""
from pathlib import Path
from typing import Dict, List

from . import config, store

HEADER = "# 学生画像（自动维护，请勿手工编辑结构）\n\n"


def ensure() -> Path:
    if not config.MEMORY_PATH.exists():
        config.MEMORY_PATH.write_text(HEADER, encoding="utf-8")
    return config.MEMORY_PATH


def read_snapshot() -> str:
    """读取记忆文件全文（会话开始时调用一次）。"""
    p = ensure()
    return p.read_text(encoding="utf-8").strip()


def append_note(topic: str, evidence: str = "") -> None:
    """把一个薄弱点写进记忆文件（会话内落盘，下一会话生效）。"""
    p = ensure()
    line = "- 薄弱点：%s%s\n" % (topic, ("（%s）" % evidence) if evidence else "")
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def render_weakness_block(limit: int = 8) -> str:
    """把数据库里的薄弱点渲染成提示词片段（volatile 层，放在系统提示词最后）。"""
    rows = store.list_weakness(limit=limit)
    if not rows:
        return "暂无记录的薄弱点。"
    lines: List[str] = []
    for r in rows:
        lines.append("- %s（出现 %d 次）" % (r["topic"], r["n"]))
    return "\n".join(lines)


def stats() -> Dict[str, object]:
    p = ensure()
    text = p.read_text(encoding="utf-8")
    return {"path": str(p), "chars": len(text), "lines": text.count("\n")}
