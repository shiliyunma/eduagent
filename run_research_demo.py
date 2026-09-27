# -*- coding: utf-8 -*-
"""研究方向调研模块 · 端到端演示（走真实 arXiv API + 真实网页抓取）

    python run_research_demo.py                       # 默认主题：教育智能体
    python run_research_demo.py "世界模型"            # 自定义主题
    python run_research_demo.py "tool learning" --offline   # 只用本地样例，不联网

演示六步流水线：
    检索 → 抓取验证 → 来源分层 → 主题地图 → 学习路线 → 落盘
并打印评测要用的两个自动指标：**权威来源占比** 与 **来源可验证率**。
"""
import os
import sys
import json

# ⚠️ 必须在 import eduagent 之前设好环境变量：
# arxiv 模块在 import 时读取 EDUAGENT_OFFLINE，决定走在线检索还是离线样例。
_ARGS = [a for a in sys.argv[1:]]
_OFFLINE = "--offline" in _ARGS
if _OFFLINE:
    os.environ["EDUAGENT_OFFLINE"] = "1"

from eduagent import config, ctx, store          # noqa: E402
from eduagent.llm.mock import MockLLM            # noqa: E402
from eduagent.loop import EduAgent               # noqa: E402


def main() -> int:
    offline = _OFFLINE
    topic = next((a for a in _ARGS if not a.startswith("--")), "教育智能体")

    print("=" * 78)
    print("研究方向调研模块 · 演示　主题：%s%s" % (topic, "（离线样例）" if offline else "（在线检索）"))
    print("=" * 78)

    store.init_db()
    agent = EduAgent(llm=MockLLM(), toolsets=["core", "research"], verbose=True)
    print("会话：%s　模型：%s　工具：%d 个\n" % (
        agent.session_id, agent.llm.name, len(agent._tool_schemas)))

    q = "帮我调研一下%s方向，我应该怎么入门？" % topic
    print("学生 > %s\n" % q)
    r = agent.run_turn(q)
    print("\n助手 > %s\n" % r["final_response"][:600].replace("\n", "\n  "))
    print("〔本轮〕步数 %d｜工具链 %s｜耗时 %.0f ms" % (
        r["iterations"], " → ".join(r["tool_calls"]), r["elapsed_ms"]))

    # ---- 评测指标（自动可算的两项）
    from pathlib import Path
    from eduagent.research import sources as src_mod, workspace as ws
    # 真实主题是模型从问句里抽出来的（可能与命令行传的不完全一致），
    # 所以这里以工作区里最后一个 json 为准 —— 演示脚本不能自己"猜"目录名。
    files = sorted(Path(config.RUN_DIR, "research").glob("*.json"),
                   key=lambda p: p.stat().st_mtime)
    if files:
        d = json.loads(files[-1].read_text(encoding="utf-8"))
        topic = d.get("topic", topic)
    else:
        d = ws.load(topic)
    items = ws.verified(d)
    stat = src_mod.summarize_tiers(items) if items else {"total": 0, "authoritative_ratio": 0,
                                                         "tier_counts": {}}
    n_all = len(d.get("candidates", []))
    n_ver = len(items)
    print("\n" + "=" * 78)
    print("【自动指标】（主题：%s）" % topic)
    print("  资料条数：抓取候选 %d → 通过验证 %d　｜　来源可验证率 %.0f%%" % (
        n_all, n_ver, 100.0 * n_ver / n_all if n_all else 0))
    print("  权威来源占比（第 1、2 层）：%.0f%%" % (stat.get("authoritative_ratio", 0) * 100))
    print("  分层分布：%s" % stat.get("tier_counts"))
    notes = d.get("notes") or []
    if notes:
        p = Path(notes[-1])
        print("\n【落盘产物】%s（%d 字符）" % (p, p.stat().st_size if p.exists() else 0))
        if p.exists():
            print("  预览前 12 行：")
            for line in p.read_text(encoding="utf-8").splitlines()[:12]:
                print("    " + line)
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
