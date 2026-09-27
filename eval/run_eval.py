# -*- coding: utf-8 -*-
"""跑自动化评测：逐题 × 逐臂执行，把逐题记录落到 eval/out/<arm>.jsonl。

用法：
    python eval/run_eval.py                        # 全部臂，mock 模型（不需要 key）
    python eval/run_eval.py --arms a2 --groups A,B # 只跑一部分
    python eval/run_eval.py --runs 3               # 每题重复 3 次（算 pass^k 用）
    LLM_API_KEY=sk-xxx python eval/run_eval.py --arms a0,a1,a2   # 用真模型

五条臂（与《03-评测与实验方案》对齐）：
    a0  纯 LLM：不检索、不给工具
    a1  纯 RAG：检索一次 → 直接把资料喂给模型生成（单轮固定流水线）
    a2  完整智能体：core 工具集（课程检索 / 习题 / 学情 / 薄弱点 / 路径）
    b0  调研单轮直出：research_search 一次 → 直接生成（调研通道的下界基线）
    b1  完整调研：core + research 工具集（六步流水线）
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EVAL_DIR))          # 让 run_eval / metrics / judge 能互相 import

import metrics as M  # noqa: E402

from eduagent import config, registry, store   # noqa: E402
from eduagent.llm import get_llm               # noqa: E402
from eduagent.loop import EduAgent             # noqa: E402

OUT_DIR = EVAL_DIR / "out"

ARMS = {
    "a0": {"name": "纯 LLM", "mode": "plain", "toolsets": []},
    "a1": {"name": "纯 RAG 固定流水线", "mode": "rag1", "toolsets": []},
    "a2": {"name": "完整智能体（core）", "mode": "agent", "toolsets": ["core"]},
    "b0": {"name": "调研单轮直出", "mode": "research1", "toolsets": []},
    "b1": {"name": "完整调研（core+research）", "mode": "agent", "toolsets": ["core", "research"]},
}
# 每条臂跑哪些组：调研组（F）只有带 research 的臂能跑
ARM_GROUPS = {
    "a0": ["A", "B", "C", "D", "E", "G"],
    "a1": ["A", "B", "C", "D", "E", "G"],
    "a2": ["A", "B", "C", "D", "E", "G"],
    "b0": ["F"],
    "b1": ["F"],
}

SYS_PLAIN = "你是一位课程助教，用简洁的中文回答学生的问题。"
SYS_RAG = ("你是一位课程助教。**只依据下面给出的课程资料**回答，不要编造资料以外的内容；"
           "回答里必须标出用到的资料出处（照抄资料里的【出处】标记）。"
           "如果资料不足以回答，直接说明资料里没有。")
SYS_KB_IDS = re.compile(r"kb\d{3,}", re.I)

MODEL_NAME = ""          # 由 main() 填上，方便报告里能标出"这轮是哪个模型跑的"
MODEL_ID = ""            # 模型 id（如 deepseek-chat / 本地 ollama 的模型名）


# ---------------------------------------------------------------- 小工具
def tool_trace(session_id: str):
    """从 trace 表拿工具调用明细：名字、成功与否、参数。"""
    out = []
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT name, latency_ms, payload FROM trace WHERE session_id=? AND kind='tool'"
            " ORDER BY id", (session_id,)).fetchall()
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        out.append({"name": r["name"], "ok": bool(payload.get("ok")),
                    "latency_ms": r["latency_ms"], "args": payload.get("args")})
    return out


def retrieved_from_messages(session_id: str):
    """从会话消息里抽出检索到的 chunk id（工具返回的正文含 【出处】kbNNN）。"""
    ids = []
    for msg in store.load_messages(session_id):
        if msg.get("role") != "tool":
            continue
        for m in SYS_KB_IDS.finditer(str(msg.get("content", ""))):
            cid = m.group(0).lower()
            if cid not in ids:
                ids.append(cid)
    return ids


def _fmt_context(rows):
    blocks = []
    for i, r in enumerate(rows, 1):
        src = " · ".join([p for p in (r.get("subject"), r.get("chapter"), r.get("section")) if p])
        cid = r.get("id") or ""
        blocks.append("[%d] 【出处】%s%s\n%s" % (i, (cid + " · ") if cid else "", src or r.get("source", ""),
                                               r.get("text", "")))
    return "\n\n".join(blocks)


def _one_shot(llm, question: str, context: str, system: str):
    user = ("下面是课程资料：\n\n%s\n\n问题：%s" % (context, question)) if context else question
    t0 = time.time()
    resp = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], [])
    return resp.content or "", round((time.time() - t0) * 1000, 1)


# ---------------------------------------------------------------- 单题执行
def run_item(arm: str, item: dict, llm, verbose=False) -> dict:
    cfg = ARMS[arm]
    mode = cfg["mode"]
    rec = {
        "arm": arm, "arm_name": cfg["name"], "model": MODEL_NAME, "model_id": MODEL_ID,
        "qid": item["qid"], "group": item["group"],
        "category": item.get("category", ""), "question": item["turns"][-1],
        "turn_answers": [], "tools_called": [], "retrieved_ids": [],
        "iterations": 0, "elapsed_ms": 0.0, "stop_reason": "stop", "session_id": "",
        "llm_calls": 0, "n_tool_calls": 0, "tool_ok_rate": None,
        "gold_ids": item.get("gold_ids") or [], "answerable": int(item.get("answerable", 1)),
        "expect_tools": item.get("expect_tools") or [], "expect": item.get("expect", "answer"),
        "error": "",
    }
    t0 = time.time()
    try:
        if mode == "agent":
            agent = EduAgent(llm=llm, toolsets=cfg["toolsets"], verbose=verbose)
            rec["session_id"] = agent.session_id
            for turn_text in item["turns"]:
                res = agent.run_turn(turn_text)
                rec["turn_answers"].append(res["final_response"])
                rec["iterations"] += res["iterations"]
                rec["stop_reason"] = res["stop_reason"]
            rec["tools_called"] = [t["name"] for t in tool_trace(agent.session_id)]
            rec["retrieved_ids"] = retrieved_from_messages(agent.session_id)
            tstats = store.trace_stats(agent.session_id)
            rec["llm_calls"] = tstats["llm_calls"]
            rec["n_tool_calls"] = tstats["tool_calls"]
            calls = tool_trace(agent.session_id)
            if calls:
                rec["tool_ok_rate"] = round(sum(1 for c in calls if c["ok"]) / len(calls), 4)

        elif mode in ("plain", "rag1", "research1"):
            context, rows = "", []
            if mode == "rag1":
                from eduagent.tools import kbsearch
                rows = (kbsearch._search_raglearn(item["turns"][-1])
                        if config.USE_RAGLEARN else kbsearch._search_sample(item["turns"][-1]))
                context = _fmt_context(rows)
                rec["retrieved_ids"] = [r.get("id", "") for r in rows if r.get("id")]
            elif mode == "research1":
                from eduagent.tools import research
                out = research.research_search(topic=item["turns"][-1], limit=8)
                context = out
                rec["tools_called"] = ["research_search"]
                rec["n_tool_calls"] = 1
                rec["tool_ok_rate"] = 1.0
            # 多轮：把历史答案拼进上下文（模拟"同一会话接着问"）
            answers = []
            for turn_text in item["turns"]:
                hist = ("\n\n（前面已经聊过：%s）" % " / ".join(answers)) if answers else ""
                ans, ms = _one_shot(llm, turn_text + hist, context,
                                    SYS_RAG if (context or mode == "rag1") else SYS_PLAIN)
                answers.append(ans)
                rec["turn_answers"].append(ans)
                rec["elapsed_ms"] += ms
            rec["llm_calls"] = len(item["turns"])
    except Exception as exc:                       # 单题失败不能中断整轮评测
        rec["error"] = repr(exc)

    answer = rec["turn_answers"][-1] if rec["turn_answers"] else ""
    rec["answer"] = answer
    rec["elapsed_ms"] = round(rec["elapsed_ms"] or (time.time() - t0) * 1000, 1)

    verdict = M.judge_auto(item, answer, rec["retrieved_ids"])
    rec.update({"score": verdict["score"], "pass": verdict["pass"],
                "refused": verdict.get("refused", False),
                "cited": verdict.get("cited", []),
                "cited_gold": verdict.get("cited_gold", False),
                "cite_valid": verdict.get("cite_valid", False),
                "reasons": verdict.get("reasons", [])})
    rec.update(M.retrieval_metrics(rec["retrieved_ids"], rec["gold_ids"]))
    rec["point_lexical_recall"] = M.point_lexical_recall(item, answer)
    if not rec.get("cited"):
        rec["cite_valid"] = False
    return rec


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="教育智能体自动化评测")
    ap.add_argument("--arms", default="a0,a1,a2,b0,b1", help="逗号分隔，见 ARMS")
    ap.add_argument("--groups", default="", help="只跑这些组，如 A,B,D")
    ap.add_argument("--qids", default="", help="只跑这些题号，如 A-01,B-02")
    ap.add_argument("--runs", type=int, default=1, help="每题重复次数（>1 时可算 pass^k）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟测试用）")
    ap.add_argument("--questions", default=str(EVAL_DIR / "questions.jsonl"))
    ap.add_argument("--tag", default="", help="给输出文件加后缀，便于对比")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    only_groups = {g.strip().upper() for g in args.groups.split(",") if g.strip()}
    only_qids = {q.strip().upper() for q in args.qids.split(",") if q.strip()}
    questions = M.load_questions(Path(args.questions))

    store.init_db()
    registry.discover()
    llm = get_llm()
    global MODEL_NAME, MODEL_ID
    MODEL_NAME = llm.name
    MODEL_ID = config.LLM_MODEL
    real = type(llm).__name__ != "MockLLM"
    print("=" * 78)
    print("教育智能体自动化评测%s" % ("" if real else "　【mock 模型：只验链路，不代表效果】"))
    print("模型：%s　题库：%d 题　臂：%s　每题跑 %d 次"
          % (llm.name, len(questions), ",".join(arms), args.runs))
    print("=" * 78)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_records = []
    for arm in arms:
        if arm not in ARMS:
            print("跳过未知臂：%s" % arm)
            continue
        todo = [q for q in questions
                if q["group"] in ARM_GROUPS[arm]
                and (not only_groups or q["group"] in only_groups)
                and (not only_qids or q["qid"].upper() in only_qids)]
        if args.limit:
            todo = todo[: args.limit]
        if not todo:
            print("[%s] 没有匹配的题，跳过" % arm)
            continue
        print("\n[%s] %s —— %d 题 × %d 次" % (arm, ARMS[arm]["name"], len(todo), args.runs))
        records = []
        t_start = time.time()
        for i, item in enumerate(todo, 1):
            for run_i in range(args.runs):
                rec = run_item(arm, item, llm, verbose=args.verbose)
                rec["run"] = run_i + 1
                records.append(rec)
                flag = "PASS" if rec["pass"] else ("0.5 " if rec["score"] == 0.5 else "FAIL")
                print("  %2d/%d %-6s %-5s score=%.1f %s  %.1fs%s"
                      % (i, len(todo), rec["qid"], flag, rec["score"],
                         ",".join(rec["tools_called"][:2]) or "无工具",
                         rec["elapsed_ms"] / 1000.0,
                         ("  ERROR:" + rec["error"]) if rec["error"] else ""))
            agg = M.aggregate(records)
            print("      …累计均分 %.3f　通过率 %.1f%%"
                  % (agg["metrics"]["E0_mean_score"]["value"],
                     agg["by_group"].get(item["group"], {}).get("pass_rate", 0) * 100))
        suffix = ("_" + args.tag) if args.tag else ""
        path = OUT_DIR / ("%s%s.jsonl" % (arm, suffix))
        with path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        agg = M.aggregate(records)
        print("  → %s（%d 条，%.1fs，均分 %.3f）"
              % (path.name, len(records), time.time() - t_start,
                 agg["metrics"]["E0_mean_score"]["value"]))
        all_records.extend(records)

    print("\n完成：共 %d 条记录，落在 %s" % (len(all_records), OUT_DIR))
    print("下一步：python eval/report.py          # 生成可视化仪表盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
