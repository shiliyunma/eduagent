# -*- coding: utf-8 -*-
"""自检脚本：克隆下来先跑这个，全绿再开始改。

    python check.py

检查 6 件事：
  1. 模块能 import（语法/依赖问题）
  2. 工具自注册生效（registry 里有 5 个工具）
  3. SQLite 读写往返一致（消息 + FTS5）
  4. 上下文估算与压缩不破坏工具对
  5. mock 模型能跑完一整轮：工具调用 → 结果回填 → 最终回答
  6. 轨迹表落到了数据（论文实验数据来源）
"""
import sys
import traceback

FAIL = []


def check(name, fn):
    try:
        info = fn()
        print("  [OK]   %-32s %s" % (name, info or ""))
    except Exception as exc:
        FAIL.append((name, exc))
        print("  [FAIL] %-32s %r" % (name, exc))
        traceback.print_exc()


def main() -> int:
    print("=" * 72)
    print("教育智能体脚手架 · 自检")
    print("=" * 72)

    def t_import():
        import eduagent  # noqa: F401
        from eduagent import context, ctx, loop, memory, registry, store  # noqa: F401
        return "版本 %s" % eduagent.__version__

    def t_registry():
        from eduagent import registry
        registry.discover()
        names = registry.names()
        assert len(names) >= 5, "工具数不足：%s" % names
        for need in ("search_course_kb", "search_exercise", "get_student_profile",
                     "update_weakness", "plan_learning_path"):
            assert need in names, "缺工具 " + need
        return "%d 个：%s" % (len(names), "、".join(names))

    def t_store():
        from eduagent import store
        store.init_db()
        sid = store.new_session("自检会话")
        store.append_message(sid, {"role": "user", "content": "自检用的消息：快速排序"})
        store.append_message(sid, {"role": "assistant", "content": "好的"})
        msgs = store.load_messages(sid)
        assert len(msgs) == 2 and msgs[0]["role"] == "user", msgs
        hits = store.search_messages("快速排序")
        assert hits, "FTS5 检索没命中"
        return "会话 %s，FTS5 命中 %d 条" % (sid[:10], len(hits))

    def t_context():
        from eduagent import context
        prompt = context.build_system_prompt("（测试）学生偏好图解", "- 循环不变式")
        for seg in ("教学原则", "学生画像快照", "本轮信息"):
            assert seg in prompt, "系统提示词缺少分层：%s" % seg
        fake = [{"role": "user", "content": "目标"}] + \
               [{"role": "assistant", "content": "x" * 4000}] * 12 + \
               [{"role": "tool", "tool_call_id": "c1", "name": "search_course_kb", "content": "y" * 200}]
        assert context.needs_compression(fake), "超预算却没触发压缩"
        new, summary = context.compress(fake)
        assert len(new) < len(fake) and summary
        assert any(m.get("role") == "tool" for m in new), "工具消息被压掉了（不许拆工具对）"
        return "估算 %d tokens → 压成 %d 条" % (context.estimate_messages(fake), len(new))

    def t_loop():
        from eduagent import store
        from eduagent.loop import EduAgent
        from eduagent.llm.mock import MockLLM
        agent = EduAgent(llm=MockLLM(), verbose=False)
        r = agent.run_turn("快速排序的时间复杂度是多少？我一直搞不懂它的稳定性，不会判断。")
        assert r["final_response"], "没有最终回答"
        assert r["tool_calls"], "一次工具都没调用"
        assert r["stop_reason"] == "stop", r["stop_reason"]
        assert r["trace"]["llm_calls"] >= 2, "模型调用次数异常：%s" % r["trace"]
        store.trace_stats(agent.session_id)
        return "步数 %d，工具 %s" % (r["iterations"], "、".join(r["tool_calls"]))

    def t_trace():
        from eduagent import store
        from eduagent.loop import EduAgent
        from eduagent.llm.mock import MockLLM
        agent = EduAgent(llm=MockLLM(), verbose=False)
        agent.run_turn("计算机网络里 TCP 为什么要三次握手？")
        stats = store.trace_stats(agent.session_id)
        assert stats["tool_calls"] >= 1 and stats["llm_calls"] >= 2, stats
        return "llm %d 次 / tool %d 次 / 合计 %.1fms" % (
            stats["llm_calls"], stats["tool_calls"], stats["total_ms"])

    check("import + 版本", t_import)
    check("工具自注册", t_registry)
    check("SQLite 读写 + FTS5", t_store)
    check("提示词分层 + 压缩", t_context)
    check("主循环（mock 一整轮）", t_loop)
    check("轨迹落表", t_trace)

    print("-" * 72)
    if FAIL:
        print("失败 %d 项，先修这些再往下做：" % len(FAIL))
        for name, exc in FAIL:
            print("  - %s：%r" % (name, exc))
        return 1
    print("全部通过。可以开始改代码了 —— 建议先加一个你自己的工具试试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
