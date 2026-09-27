# -*- coding: utf-8 -*-
"""命令行入口：python -m eduagent.cli "你的问题"

例子：
    python -m eduagent.cli "快速排序的时间复杂度是多少"        # 有 key 就用真模型
    python -m eduagent.cli --mock "快速排序稳定吗"              # 强制用假模型（免 key）
    python -m eduagent.cli --tools                              # 看注册了哪些工具
    python -m eduagent.cli --session s_xxxxxxxx "接着上面的问题"
    python -m eduagent.cli                                      # 交互模式
"""
import argparse
import sys

from . import config, memory, registry, store
from .llm import get_llm


def _print_tools() -> None:
    registry.discover()
    print("已注册工具（%d 个）：" % len(registry.names()))
    for spec in registry.get_tools():
        print("  [%s] %-22s %s" % (spec.toolset, spec.name, spec.description[:60]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="eduagent", description="教育智能体运行时（毕设脚手架）")
    p.add_argument("question", nargs="*", help="问题；不传则进入交互模式")
    p.add_argument("--mock", action="store_true", help="用假模型（不需要 API key）")
    p.add_argument("--session", default=None, help="续用已有会话 ID")
    p.add_argument("--toolsets", default=None, help="只启用这些工具集，逗号分隔")
    p.add_argument("--tools", action="store_true", help="列出工具后退出")
    p.add_argument("--quiet", action="store_true", help="少打印过程")
    p.add_argument("--max-iterations", type=int, default=None)
    args = p.parse_args(argv)

    store.init_db()
    memory.ensure()

    if args.tools:
        _print_tools()
        return 0

    from .loop import EduAgent

    toolsets = args.toolsets.split(",") if args.toolsets else None
    llm = get_llm("mock" if args.mock else "auto")
    agent = EduAgent(llm=llm, toolsets=toolsets, session_id=args.session,
                     max_iterations=args.max_iterations, verbose=not args.quiet)
    print("会话：%s　模型：%s　工具：%d 个" % (
        agent.session_id, agent.llm.name, len(agent._tool_schemas)))

    def one(q: str) -> None:
        r = agent.run_turn(q)
        print("\n助手 > %s\n" % r["final_response"])
        print("[本轮] 步数 %d｜工具 %s｜结束原因 %s｜耗时 %.0fms｜模型调用 %d 次" % (
            r["iterations"], "、".join(r["tool_calls"]) or "无", r["stop_reason"],
            r["elapsed_ms"], r["trace"]["llm_calls"]))

    if args.question:
        one(" ".join(args.question))
        return 0

    print("交互模式（输入 exit 退出）")
    while True:
        try:
            q = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "退出"):
            break
        one(q)
    return 0


if __name__ == "__main__":
    sys.exit(main())
