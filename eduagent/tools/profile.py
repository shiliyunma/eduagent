# -*- coding: utf-8 -*-
"""学情与学习路径工具 —— 这三个是"教育智能体"和"通用问答机器人"的分界线。

通用问答机器人：问一次答一次，无状态。
教育智能体：    会记住你哪里薄弱、会据此调整讲解顺序、会给学习路径。
"""
import json
from pathlib import Path
from typing import Any, Dict, List

from .. import config, ctx, memory, store
from ..registry import register

_SYLLABUS = config.DATA_DIR / "syllabus.json"


def _load_syllabus() -> Dict[str, List[str]]:
    if _SYLLABUS.exists():
        return json.loads(_SYLLABUS.read_text(encoding="utf-8"))
    return {}


def _check_db() -> bool:
    return config.DB_PATH.exists()


@register(
    name="get_student_profile",
    description=("查询当前学生的学习画像：历史薄弱点、提问次数最多的知识点。"
                 "在讲解新知识前调用它，可以做到因材施教。无参数。"),
    parameters={"type": "object", "properties": {}},
    toolset="core",
    check_fn=_check_db,
)
def get_student_profile() -> str:
    rows = store.list_weakness(limit=8)
    total = sum(int(r["n"]) for r in rows)
    recent = store.search_messages("", limit=0) if False else []
    lines = ["该学生当前记录在案的薄弱点（共 %d 条记录）：" % total]
    if rows:
        for r in rows:
            lines.append("- %s（出现 %d 次）%s" % (
                r["topic"], r["n"], ("证据：" + r["evidence"][:50]) if r.get("evidence") else ""))
    else:
        lines.append("- 暂无（这是第一次对话，可以先做一次摸底）")
    lines.append("\n历史提问（最近）：")
    try:
        with store.connect() as conn:
            qs = conn.execute(
                "SELECT DISTINCT content FROM messages WHERE role='user' ORDER BY id DESC LIMIT 5"
            ).fetchall()
        lines += ["- " + (q["content"] or "")[:60] for q in qs] or ["- 无"]
    except Exception:
        lines.append("- 无")
    return "\n".join(lines)


@register(
    name="update_weakness",
    description=("记录一个知识薄弱点，用于后续因材施教。"
                 "只在有明确证据时调用：学生说不会/做错了，或连续追问同一个概念。"),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "知识点名称，尽量具体，如 快速排序的稳定性"},
            "evidence": {"type": "string", "description": "证据：学生原话或出错点"},
        },
        "required": ["topic"],
    },
    toolset="core",
    check_fn=_check_db,
)
def update_weakness(topic: str, evidence: str = "") -> str:
    sid = ctx.current_session()
    store.save_weakness(sid, topic, evidence)
    memory.append_note(topic, evidence)          # 同时写进 MEMORY.md（下次会话生效）
    return "已记录薄弱点：%s。下次讲解会优先覆盖它。" % topic


@register(
    name="plan_learning_path",
    description=("按目标和剩余天数生成学习路径，会优先安排学生已记录的薄弱点。"
                 "学生问「怎么复习/几天够不够/先学什么」时用它。"),
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "学习目标，如 数据结构期末 80 分"},
            "days": {"type": "integer", "description": "可用天数，默认 7", "default": 7},
            "subject": {"type": "string", "description": "科目，可选"},
        },
        "required": ["goal"],
    },
    toolset="core",
    check_fn=_check_db,
)
def plan_learning_path(goal: str, days: int = 7, subject: str = None) -> str:
    days = max(1, int(days))
    weak = [r["topic"] for r in store.list_weakness(limit=6)]
    syllabus = _load_syllabus()
    chapters: List[str] = []
    for subj, chs in syllabus.items():
        if subject and subject not in subj:
            continue
        chapters += ["%s：%s" % (subj, c) for c in chs]
    # v1 策略：薄弱点优先，其余按大纲顺序铺满
    plan: List[str] = []
    per_day = max(1, len(chapters) // days + 1)
    idx = 0
    for d in range(1, days + 1):
        part: List[str] = []
        if weak and d <= len(weak):
            part.append("复盘薄弱点：%s" % weak[d - 1])
        for _ in range(per_day):
            if idx < len(chapters):
                part.append(chapters[idx])
                idx += 1
        if not part:
            part = ["机动：做题 + 错题回顾"]
        plan.append("第 %d 天：%s" % (d, "；".join(part)))
    head = "目标：%s　可用 %d 天\n（策略：已记录的薄弱点优先复盘，其余按大纲顺序推进）\n" % (goal, days)
    tail = "\n\n提示：这是基于课程大纲生成的初版计划，执行中会根据你每天的完成情况调整。"
    return head + "\n".join(plan) + tail
