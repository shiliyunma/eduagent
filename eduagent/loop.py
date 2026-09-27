# -*- coding: utf-8 -*-
"""主循环 —— 整个毕设的心脏。

一次 turn 的生命周期（论文"系统设计"里就是这张图）：
  1. 把用户消息落库
  2. 组装 messages：[系统提示词] + 历史 + 本轮
  3. 【预算检查】超阈值就先压缩
  4. 调模型
  5. 按 finish_reason 分支：
        tool_calls → 执行工具（1 个串行 / 多个并发）→ 结果作为 tool 消息回填 → 回到 3
        stop       → 出答案，落库，结束
        length     → 压缩后重试
  6. 迭代预算耗尽 → 返回"已完成的部分 + 原因"，绝不无声死循环

三条不变量（照 Hermes 的分层学的，但这里是自己实现）：
  A. 系统提示词在会话内**建一次就复用**（前缀缓存不破）
  B. 消息角色严格交替；只有 tool 可以连续
  C. 每一次模型调用 / 工具调用都落 trace 表 —— 论文的实验数据从这来，不靠事后回忆
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from . import config, context, ctx, memory, registry, store
from .llm import get_llm
from .protocols import LLMResponse, ToolCall

TRUNCATE_RESULT = 4000          # 单个工具结果入库/回灌的最大长度


class EduAgent:
    def __init__(self, llm=None, toolsets: Optional[List[str]] = None,
                 session_id: Optional[str] = None, max_iterations: Optional[int] = None,
                 verbose: bool = True):
        store.init_db()
        registry.discover()                       # import 工具包 → 触发自注册
        self.llm = llm or get_llm()
        self.toolsets = toolsets
        self.session_id = session_id or store.new_session("新会话")
        self.max_iterations = max_iterations or config.MAX_ITERATIONS
        self.verbose = verbose
        self._sys_prompt: Optional[str] = None    # 不变量 A：会话内只建一次
        self._tool_schemas = registry.schemas(self.toolsets)

    # ------------------------------------------------------------ 对外
    def run_turn(self, user_message: str) -> Dict[str, Any]:
        t0 = time.time()
        store.append_message(self.session_id, {"role": "user", "content": user_message})
        history = store.load_messages(self.session_id)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}] + history
        tools = self._tool_schemas
        iterations = 0
        tool_calls_made: List[str] = []
        final_text = ""
        stop_reason = "stop"

        while True:
            if iterations >= self.max_iterations:
                stop_reason = "max_iterations"
                final_text = ("已达到本轮最大步数（%d 步），先说到这里。"
                              "已完成的部分在上面；继续追问我会接着往下做。" % self.max_iterations)
                break
            iterations += 1

            # 3. 预算检查 + 压缩（压缩只动中间段，保头保尾、工具对不拆）
            if context.needs_compression(messages):
                messages, summary = context.compress(messages)
                if self.verbose and summary:
                    self._log("压缩上下文：%s" % summary)

            ctx.set_session(self.session_id, iterations)

            # 4. 调模型
            n = 1
            while n <= 2:
                try:
                    resp = self._call_llm(messages, tools, iterations)
                    break
                except Exception as exc:
                    if n == 2:
                        raise
                    self._log("模型调用失败，重试一次：%r" % (exc,))
                    time.sleep(1.5)
                    n += 1

            # 5. 分支
            if resp.finish_reason == "length":
                self._log("模型返回 length：压缩后重试")
                messages, _ = context.compress(messages)
                continue

            if not resp.tool_calls:
                final_text = resp.content or ""
                store.append_message(self.session_id, {"role": "assistant", "content": final_text})
                break

            # 有工具调用：先把 assistant 消息（含 tool_calls）落库
            assistant_msg = {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.name,
                                             "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                               for c in resp.tool_calls],
            }
            messages.append(assistant_msg)
            store.append_message(self.session_id, assistant_msg)

            results = self._run_tools(resp.tool_calls, iterations)
            for call, out in results:
                tool_calls_made.append(call.name)
                msg = {"role": "tool", "tool_call_id": call.id, "name": call.name,
                       "content": out[:TRUNCATE_RESULT]}
                messages.append(msg)
                store.append_message(self.session_id, msg)

        store.touch_session(self.session_id)
        elapsed = round((time.time() - t0) * 1000, 1)
        stats = store.trace_stats(self.session_id)
        return {
            "session_id": self.session_id,
            "final_response": final_text,
            "iterations": iterations,
            "tool_calls": tool_calls_made,
            "stop_reason": stop_reason,
            "elapsed_ms": elapsed,
            "trace": stats,
        }

    def chat(self, user_message: str) -> str:
        return self.run_turn(user_message)["final_response"]

    # ------------------------------------------------------------ 内部
    def system_prompt(self) -> str:
        if self._sys_prompt is None:
            snap = memory.read_snapshot()
            self._sys_prompt = context.build_system_prompt(snap, memory.render_weakness_block())
        return self._sys_prompt

    def _call_llm(self, messages, tools, iteration: int) -> LLMResponse:
        t = time.time()
        resp = self.llm.chat(messages, tools)
        ms = round((time.time() - t) * 1000, 1)
        store.trace(self.session_id, 0, iteration, "llm", self.llm.name, ms, {
            "finish_reason": resp.finish_reason,
            "tool_calls": [c.name for c in resp.tool_calls],
            "content_chars": len(resp.content or ""),
            "usage": resp.usage,
        })
        if self.verbose:
            self._log("模型第 %d 次调用：%s（%sms）%s" % (
                iteration, resp.finish_reason, ms,
                ("→ " + "、".join(c.name for c in resp.tool_calls)) if resp.tool_calls else ""))
        return resp

    def _run_tools(self, calls: List[ToolCall], iteration: int):
        """1 个调用或含交互型工具 → 串行；多个 → 并发；结果按原顺序回填。"""
        specs = {c.name: registry.get(c.name) for c in calls}
        interactive = any(s and s.interactive for s in specs.values())
        if len(calls) == 1 or interactive:
            return [self._exec_one(c, iteration) for c in calls]
        with ThreadPoolExecutor(max_workers=min(config.TOOL_CONCURRENCY, len(calls))) as pool:
            futures = [pool.submit(self._exec_one, c, iteration) for c in calls]
            return [f.result() for f in futures]          # 顺序 = 原调用顺序

    def _exec_one(self, call: ToolCall, iteration: int):
        ctx.set_session(self.session_id, iteration)      # 线程内补设上下文
        spec = registry.get(call.name)
        t = time.time()
        if spec is None:
            out = "工具 %s 不存在。可用的工具：%s" % (call.name, "、".join(registry.names()))
            ok = False
        else:
            try:
                out = spec.handler(**(call.arguments or {}))
                ok = True
            except TypeError as exc:                      # 参数不对：让模型自己改
                out = "工具参数不正确：%r。请按 schema 重新调用。" % (exc,)
                ok = False
            except Exception as exc:
                out = "工具执行失败：%r" % (exc,)
                ok = False
        out = str(out)
        ms = round((time.time() - t) * 1000, 1)
        store.trace(self.session_id, 0, iteration, "tool", call.name, ms,
                    {"ok": ok, "args": call.arguments, "out_chars": len(out)})
        if self.verbose:
            self._log("  工具 %s（%sms）%s → %s" % (call.name, ms, "OK" if ok else "ERR",
                                                  out.replace("\n", " ")[:70]))
        return call, out

    def _log(self, msg: str) -> None:
        print("  " + msg)
