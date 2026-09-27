# -*- coding: utf-8 -*-
"""学习路线生成：把资料清单排成"先学什么、后学什么、大概花多久"。

算法是刻意做成确定性、可解释的 —— 答辩问"你的路线是怎么生成的"，
必须能答出一套规则，而不是"让模型自由发挥"。

五阶段（对应认知顺序）：
    0 预备知识   —— 由调用方传入的先修项（如"会 Python + 懂基本 HTTP"）
    1 建立全局观 —— 综述 / 教程 / 官方 overview（门槛低、覆盖全）
    2 核心精读   —— 论文原文（第 1 层来源）
    3 动手实践   —— 官方文档 / 代码仓库
    4 前沿跟踪   —— 最近 12 个月的预印本
时间分配按权重：预备 5% / 全局 15% / 核心 40% / 实践 30% / 前沿 10%。
"""
import time
from typing import Any, Dict, List

WEIGHTS = {"预备知识": 0.05, "建立全局认识": 0.15, "核心论文精读": 0.40,
           "动手实践": 0.30, "前沿跟踪": 0.10}

FRONTIER_MONTHS = 12


def _is_survey(it: Dict[str, Any]) -> bool:
    t = (it.get("title") or "").lower()
    return any(k in t for k in ("survey", "review", "tutorial", "overview", "综述", "教程"))


def _is_practice(it: Dict[str, Any]) -> bool:
    u = (it.get("url") or "").lower()
    t = (it.get("title") or "").lower()
    return ("github.com" in u or "/docs" in u or "docs." in u or ".io/" in u
            or any(k in t for k in ("toolkit", "library", "framework", "documentation",
                                    "repo", "官方文档", "开发文档")))


def _is_overview(it: Dict[str, Any]) -> bool:
    """综述/教程/官方概览 —— 这一类适合'建立全局认识'阶段。

    ⚠️ 注意别写成 `tier <= 2`：那会把核心论文也一起吃掉，
    导致'核心论文精读'阶段空掉（这个坑在第一次跑演示时踩到了）。
    """
    if _is_practice(it):
        return False
    return _is_survey(it) or it.get("tier") == 2


def _is_frontier(it: Dict[str, Any]) -> bool:
    y = it.get("year") or 0
    return y >= time.localtime().tm_year - (1 if time.localtime().tm_mon > 1 else 2) and \
        bool(it.get("arxiv_id"))


def plan_route(topic: str, items: List[Dict[str, Any]], hours: float = 20.0,
               level: str = "初学", prereq: str = "") -> Dict[str, Any]:
    used: List[str] = []

    def take(pred, limit):
        out = []
        for it in items:
            if it.get("url") in used:
                continue
            if pred(it):
                out.append(it)
                used.append(it.get("url"))
                if len(out) >= limit:
                    break
        return out

    phases: List[Dict[str, Any]] = []

    if prereq:
        phases.append({
            "name": "预备知识", "hours": round(hours * WEIGHTS["预备知识"], 1),
            "items": [{"title": p.strip(), "url": "", "tier": None}
                      for p in prereq.replace("；", ";").replace("、", ";").split(";") if p.strip()],
            "deliverable": "确认自己能看懂示例代码；不会的先补",
        })
    phases.append({
        "name": "建立全局认识", "hours": round(hours * WEIGHTS["建立全局认识"], 1),
        "items": take(_is_overview, 3),
        "deliverable": "用自己的话写 200 字：这个方向解决什么问题、有哪几类做法",
    })
    phases.append({
        "name": "核心论文精读", "hours": round(hours * WEIGHTS["核心论文精读"], 1),
        "items": take(lambda x: x.get("tier") == 1 and not _is_frontier(x), 5),
        "deliverable": "每篇 1 页笔记：问题 / 方法 / 结论 / 局限",
    })
    phases.append({
        "name": "动手实践", "hours": round(hours * WEIGHTS["动手实践"], 1),
        "items": take(_is_practice, 3) or take(lambda x: x.get("tier", 9) <= 2, 3),
        "deliverable": "跑通一个最小示例，代码进 git",
    })
    frontier = take(_is_frontier, 4)
    if frontier:
        phases.append({
            "name": "前沿跟踪", "hours": round(hours * WEIGHTS["前沿跟踪"], 1),
            "items": frontier,
            "deliverable": "列出 3 个还没解决的问题（这就是可选题方向）",
        })

    tot = sum(p["hours"] for p in phases) or hours
    return {
        "topic": topic, "level": level, "hours_planned": hours, "hours_allocated": round(tot, 1),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "phases": phases,
        "rule": "按'综述→论文→实践→前沿'的认知顺序排；时间按 5/15/40/30/10 配比；同层内按来源层级优先",
    }


def render_route(route: Dict[str, Any]) -> str:
    lines = ["学习路线：%s（%s，计划 %.0f 小时）" % (
        route["topic"], route["level"], route["hours_planned"]),
        "生成规则：%s" % route["rule"], ""]
    for i, p in enumerate(route["phases"], 1):
        lines.append("阶段 %d · %s（约 %.1f 小时）" % (i, p["name"], p["hours"]))
        for it in p["items"]:
            tier = ("[%d层]" % it["tier"]) if it.get("tier") else ""
            url = ("  " + it["url"]) if it.get("url") else ""
            lines.append("   - %s %s%s" % (tier, it["title"], url))
        lines.append("   产出：%s" % p["deliverable"])
        lines.append("")
    return "\n".join(lines)
