# -*- coding: utf-8 -*-
"""上下文与提示词组装。

三层分级（照 Hermes 的做法，实现是自己写的）：
  stable   —— 人设、工具使用规范、教学原则：整场会话不变
  context  —— 记忆快照、学情画像：会话开始时定一次
  volatile —— 时间戳、预算告警：每轮都可能变，放最后

为什么必须分级：前缀缓存只认"从头开始一字不变"的公共前缀。
把易变内容放前面 = 每轮缓存全废。所以 volatile 永远在最后。

压缩策略：超过预算 * COMPRESS_THRESHOLD 时，保住头部（目标/约束）与最近 N 条，
中间段替换成一条带标记的占位摘要。工具调用与它的结果成对保留，不许拆开。
"""
import re
import time
from typing import Any, Dict, List, Tuple

from . import config

CJK = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中文按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。

    正式实验时换成模型的 tokenizer（或让 API 返回 usage），这里只用于预算控制。
    """
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    other = len(text) - cjk
    return int(cjk + other / 4)


def estimate_messages(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(str(m.get("content") or ""))
        if m.get("tool_calls"):
            total += estimate_tokens(str(m["tool_calls"]))
        total += 4                      # 每条消息的结构开销
    return total


def build_system_prompt(memory_snapshot: str, weakness_block: str) -> str:
    stable = """你是面向高校课程学习的教育智能体。你的服务对象是正在准备课程考试的学生。

【教学原则】
1. 可追溯：回答必须基于检索到的课程资料。引用时给出【出处】，没有资料支撑就明确说"资料里没有"，不要编。
2. 先查再答：涉及具体知识点的问题，先用检索工具查课程知识库，再作答。
3. 因材施教：回答前先了解这个学生的薄弱点，讲解时优先覆盖他薄弱的部分。
4. 不给答案给过程：涉及练习时，先给思路与提示，再给解答；学生说不会才展开。
5. 不越界：不承诺分数、不替代教师给成绩。

【工具使用规范】
- 你有多轮对话能力：一次任务里可以连续调用多个工具。
- 调用工具前想清楚要什么；工具返回空结果时换关键词重试，最多重试一次。
- 记录薄弱点要基于证据（学生的错误、明确表示不会），不要凭猜测写入。

【输出格式】
- 中文、条理清晰；关键结论用【出处】标注来源文件与章节。
- 单次回答不超过 400 字，除非学生要求展开。"""

    context_layer = """【学生画像快照】
%s

【已知薄弱点】
%s""" % (memory_snapshot or "（空）", weakness_block or "（无）")

    volatile = "【本轮信息】当前时间：%s" % time.strftime("%Y-%m-%d %H:%M")

    # 三层用双换行拼接：stable -> context -> volatile
    return "\n\n".join([stable, context_layer, volatile])


def needs_compression(messages: List[Dict[str, Any]]) -> bool:
    budget = config.CONTEXT_BUDGET_TOKENS
    return estimate_messages(messages) > budget * config.COMPRESS_THRESHOLD


def compress(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
    """压缩对话历史：保头 + 保尾 + 中间换成占位摘要。

    返回 (新消息列表, 摘要文本)。注意：**工具调用与它的结果成对保留**。
    """
    keep = config.PROTECT_LAST_N
    head = messages[:1]                                  # 首条用户消息 = 本轮目标
    tail = messages[-keep:]
    middle = messages[1:-keep]
    if not middle:
        return messages, ""

    # 统计被压掉的工具调用，写进摘要，保证模型知道"我之前查过什么"
    tools_used = []
    for m in middle:
        for tc in (m.get("tool_calls") or []):
            fn = (tc.get("function") or {}).get("name") if isinstance(tc, dict) else None
            if fn:
                tools_used.append(fn)
    summary = "[已压缩 %d 条历史消息；此前调用过的工具：%s]" % (
        len(middle), "、".join(sorted(set(tools_used))) or "无")
    return head + [{"role": "assistant", "content": summary}] + tail, summary
