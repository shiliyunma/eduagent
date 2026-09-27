# -*- coding: utf-8 -*-
"""一键演示：不需要 API key，跑完能看到完整证据链。

    python run_demo.py

演示三段对话，覆盖毕设要证明的四件事：
  ① 多步工具调用（先看学情 → 再查资料 → 再作答）
  ② 结果回填（工具结果作为 tool 消息回到模型）
  ③ 状态累积（把"搞不懂"记成薄弱点，写进 SQLite 与 MEMORY.md）
  ④ 跨会话记忆（新开会话时，系统提示词里已经带上这个薄弱点）
"""
import json
import sys

from eduagent import config, memory, registry, store
from eduagent.llm.mock import MockLLM
from eduagent.loop import EduAgent

QUESTIONS = [
    "快速排序的时间复杂度是多少？",
    "我一直搞不懂快排的稳定性，考试总是判断错，不会分析。",
    "计算机网络里 TCP 为什么要三次握手？",
]


def main() -> int:
    print("=" * 78)
    print("教育智能体 · 端到端演示（mock 模型，不需要 API key）")
    print("=" * 78)

    store.init_db()
    memory.ensure()

    print("\n【已注册工具】import 即注册，没有中心清单文件")
    registry.discover()
    for spec in registry.get_tools():
        print("   %-22s [%s] %s" % (spec.name, spec.toolset, spec.description[:52]))

    agent = EduAgent(llm=MockLLM(), verbose=True)
    print("\n会话 ID：%s" % agent.session_id)

    for i, q in enumerate(QUESTIONS, 1):
        print("\n" + "-" * 78)
        print("第 %d 轮  学生 > %s" % (i, q))
        print("-" * 78)
        r = agent.run_turn(q)
        print("\n  助手 > %s" % (r["final_response"][:400].replace("\n", "\n  ")))
        print("  〔本轮〕步数 %d｜工具 %s｜结束原因 %s｜耗时 %.0f ms"
              % (r["iterations"], "、".join(r["tool_calls"]) or "无",
                 r["stop_reason"], r["elapsed_ms"]))

    # ---- 证据一：轨迹统计（论文实验数据来源）
    stats = store.trace_stats(agent.session_id)
    print("\n" + "=" * 78)
    print("【证据 1】轨迹统计（自动落 trace 表，论文的实验数据就是从这里出的）")
    print("        模型调用 %d 次｜工具调用 %d 次｜模型耗时 %.1f ms｜工具耗时 %.1f ms｜合计 %.1f ms"
          % (stats["llm_calls"], stats["tool_calls"], stats["llm_ms"],
             stats["tool_ms"], stats["total_ms"]))

    # ---- 证据二：数据库里到底存了什么
    print("\n【证据 2】SQLite 里的会话与消息（跨进程存活）")
    with store.connect() as conn:
        n_msg = conn.execute("SELECT COUNT(*) c FROM messages WHERE session_id=?",
                             (agent.session_id,)).fetchone()["c"]
        roles = conn.execute("SELECT role, COUNT(*) c FROM messages WHERE session_id=? GROUP BY role",
                             (agent.session_id,)).fetchall()
    print("        消息 %d 条：%s" % (n_msg, "，".join("%s×%d" % (r["role"], r["c"]) for r in roles)))

    # ---- 证据三：学情画像（教育智能体与通用问答的分界线）
    weak = store.list_weakness()
    print("\n【证据 3】学情画像（Agent 自己决定记录下来的薄弱点）")
    for w in weak:
        print("        - %s（%d 次）证据：%s" % (w["topic"], w["n"], (w["evidence"] or "")[:44]))

    # ---- 证据四：跨会话记忆
    print("\n【证据 4】跨会话记忆：新开会话时，系统提示词里已经带上了上面的薄弱点")
    fresh = EduAgent(llm=MockLLM(), verbose=False)
    prompt = fresh.system_prompt()
    hit = [w["topic"] for w in weak if w["topic"] in prompt]
    print("        新会话 %s 的系统提示词命中薄弱点：%s" % (fresh.session_id, hit or "（无）"))
    print("        系统提示词长度：%d 字符（三层：stable + context + volatile）" % len(prompt))

    # ---- 证据五：记忆文件
    print("\n【证据 5】MEMORY.md 快照（会话开始时读一次，会话内不再变 —— 保前缀缓存）")
    print("        路径：%s" % config.MEMORY_PATH)
    print("        行数：%d" % memory.stats()["lines"])

    # ---- 证据六：直接查工具（不经过模型）
    print("\n【证据 6】工具可单独调用（便于写单元测试）")
    from eduagent.tools.kbsearch import search_course_kb
    from eduagent.tools.profile import plan_learning_path
    out = search_course_kb("B+树 索引")
    print("        search_course_kb → %s" % out.replace("\n", " ")[:100])
    plan = plan_learning_path("数据结构期末 80 分", 3)
    print("        plan_learning_path → %s" % plan.split("\n")[2][:100])

    print("\n" + "=" * 78)
    print("演示结束。下一步：把 config.USE_RAGLEARN 改成 1 接上 RAGLearn 的真实检索，")
    print("或者直接看计划书里的《05-第一周开工清单》。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
