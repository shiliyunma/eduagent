# -*- coding: utf-8 -*-
"""剧本式假模型 —— 不需要任何 API key 就能把整条链路跑通。

它不是一个真的模型，而是一个"够像样的替身"，用来在没有 key / 断网 / 单元测试时
把主循环、工具调用、结果回填、多轮记忆验证一遍。

行为规则（刻意做成通用规则，不写死某一个问题的答案）：
  第 1 步：还没调过工具 → 按问题关键词挑一个工具，参数从用户原话里取
  第 2 步：调过一次 → 如果问题里出现"不会/不懂/错"，再调 update_weakness 记一笔
  第 3 步：之后 → 把最后一条工具结果汇总成最终回答
"""
import json
import re
import time
from typing import Any, Dict, List, Optional

from ..protocols import LLMResponse, ToolCall
from .base import LLM

WEAK_HINT = ("不会", "不懂", "没听懂", "错了", "做错", "搞不清", "记不住")
EXERCISE_HINT = ("习题", "题目", "练习", "真题", "考题", "刷题")
# 研究方向调研类问题 —— 这类问题走的是 research 工具集（另一条流水线）
RESEARCH_HINT = ("怎么学", "如何学", "入门", "学习路线", "研究路线", "研究方向", "调研",
                 "有哪些资料", "该看什么", "最新进展", "怎么开始")

RESEARCH_SEQ = ["research_search", "fetch_source", "grade_sources",
                "build_topic_map", "plan_learning_route", "save_research_note"]

_TOPIC_NOISE = ("帮我", "我想", "请问", "请", "怎么学", "如何学", "怎么开始", "入门", "学习路线",
                "研究路线", "研究方向", "调研", "有哪些资料", "该看什么", "最新进展", "一下",
                "的", "了", "呢", "吗", "？", "?", "我", "要", "想", "规划", "学习", "方向")


def _extract_topic(q: str) -> str:
    """从问句里抠出"研究方向"这个主题词。真实系统应交给模型抽取，这里用规则保证可测。"""
    s = q
    for w in _TOPIC_NOISE:
        s = s.replace(w, " ")
    parts = [p.strip(" ，,。.、:：") for p in s.split()]
    parts = [p for p in parts if len(p) >= 2]
    if not parts:
        return q[:20]
    return max(parts, key=len)[:30]


def _last_user(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _last_tool_content(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "tool":
            return str(m.get("content") or "")
    return ""


def _tool_calls_since_user(messages: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for m in reversed(messages):
        if m.get("role") == "user":
            break
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                fn = (tc.get("function") or {}).get("name") if isinstance(tc, dict) else None
                if fn:
                    seen.append(fn)
    return seen


def _demo_arg(name: str, question: str, schemas: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """按工具 schema 生成一份合理参数（从用户问题里抠关键词）。"""
    spec = schemas.get(name, {})
    props = (spec.get("parameters") or {}).get("properties", {})
    args: Dict[str, Any] = {}
    topic = re.sub(r"[?？。，,、\s]+", " ", question).strip()[:30] or "课程问题"
    for key, meta in props.items():
        t = meta.get("type", "string")
        if key in ("query", "keyword", "q", "text", "topic", "question"):
            args[key] = topic
        elif key in ("n", "count", "top_k"):
            args[key] = 5
        elif key in ("days",):
            args[key] = 7
        elif key in ("hours",):
            args[key] = 20
        elif key in ("limit",):
            args[key] = 8
        elif key in ("max_items",):
            args[key] = 3
        elif t == "string":
            args[key] = meta.get("default", "")
        elif t == "integer":
            args[key] = int(meta.get("default", 0))
    return args


class MockLLM(LLM):
    name = "mock"

    def __init__(self, delay_ms: int = 5, **_ignored):
        self.delay_ms = delay_ms

    def chat(self, messages, tools=None) -> LLMResponse:
        time.sleep(self.delay_ms / 1000.0)
        question = _last_user(messages)
        used = _tool_calls_since_user(messages)
        schemas = {}
        for t in (tools or []):
            fn = t.get("function") or {}
            if fn.get("name"):
                schemas[fn["name"]] = fn        # 存 function 层（parameters 在这里面）

        # 第 0 分支：研究方向调研类问题 → 走 research 六步流水线
        # （刻意放在最前面：这类问题不该先查学情、也不该去课程库里找答案）
        if "research_search" in schemas and any(h in question for h in RESEARCH_HINT):
            topic = _extract_topic(question)
            nxt = next((s for s in RESEARCH_SEQ if s in schemas and s not in used), None)
            if nxt:
                args = _demo_arg(nxt, question, schemas)
                args["topic"] = topic
                args.pop("query", None)     # 让工具自己做中→英关键词映射，别把整句问话塞进检索
                return LLMResponse(
                    content="调研流程第 %d 步：%s" % (len(used) + 1, nxt),
                    tool_calls=[ToolCall(id="call_res_%d" % (len(used) + 1),
                                         name=nxt, arguments=args)],
                    finish_reason="tool_calls",
                )
            # 六步走完 → 直接收尾，不要再回去调课程库的工具
            return LLMResponse(
                content="【调研完成】我已经把「%s」方向的资料分层、主题地图与学习路线整理好了：\n\n%s\n\n"
                        "（这是 mock 模型生成的回答，用于验证链路。配置 LLM_API_KEY 后会换成真模型。）"
                        % (topic, _last_tool_content(messages)[:400]),
                finish_reason="stop",
            )

        # 第 1 步：先了解学生（有 get_student_profile 就先调），否则按关键词挑检索工具
        if not used:
            if "get_student_profile" in schemas:
                pick = "get_student_profile"
            elif any(h in question for h in EXERCISE_HINT) and "search_exercise" in schemas:
                pick = "search_exercise"
            elif "search_course_kb" in schemas:
                pick = "search_course_kb"
            else:
                pick = next(iter(schemas), None)
            if pick:
                args = _demo_arg(pick, question, schemas)
                return LLMResponse(
                    content="我先了解一下你之前的情况。",
                    tool_calls=[ToolCall(id="call_mock_1", name=pick, arguments=args)],
                    finish_reason="tool_calls",
                )

        # 第 2 步：检索还没做 → 做一次检索
        if not any(u.startswith("search_") for u in used):
            pick = "search_exercise" if any(h in question for h in EXERCISE_HINT) else "search_course_kb"
            if pick in schemas:
                args = _demo_arg(pick, question, schemas)
                return LLMResponse(
                    content="我去课程资料里查一下。",
                    tool_calls=[ToolCall(id="call_mock_2", name=pick, arguments=args)],
                    finish_reason="tool_calls",
                )

        # 第 3 步：学生表达了困难 → 记一笔薄弱点（演示"写记忆"这条支线）
        if (any(h in question for h in WEAK_HINT)
                and "update_weakness" in schemas
                and not any(u == "update_weakness" for u in used)):
            args = _demo_arg("update_weakness", question, schemas)
            args["topic"] = re.sub(r"[?？。，,、\s]+", " ", question).strip()[:20] or "未命名知识点"
            args["evidence"] = question[:60]
            return LLMResponse(
                content="我先记一下这个薄弱点，下次优先讲。",
                tool_calls=[ToolCall(id="call_mock_3", name="update_weakness", arguments=args)],
                finish_reason="tool_calls",
            )

        # 结束：把最后一条工具结果汇总成回答
        last_tool = ""
        for m in reversed(messages):
            if m.get("role") == "tool":
                last_tool = str(m.get("content") or "")
                break
        answer = (
            "【模拟回答】根据检索到的课程资料：\n\n%s\n\n"
            "（这是 mock 模型生成的回答，用于验证链路。配置 LLM_API_KEY 后会换成真模型。）"
            % (last_tool[:300] if last_tool else "（没有检索到内容）"))
        return LLMResponse(content=answer, finish_reason="stop",
                           usage={"prompt_tokens": 0, "completion_tokens": 0})
