# -*- coding: utf-8 -*-
"""第 3 层指标：LLM 判官（G-Eval 风格：先逐条列证据再打分）。

为什么要有这一层：第 1、2 层只能查"引没引、引的对不对"，
**查不出"答案内容是不是被资料支持"**。那是语义判断，要么人工，要么判官。

用法：
    python eval/judge.py --arm a2                  # 判 a2 的全部记录
    python eval/judge.py --arm a2 --limit 20       # 只判前 20 条（省时间）
    python eval/judge.py --arm a2 --sample 20      # 随机抽 20 条
    python eval/judge.py --kappa out/judged_a2.jsonl out/manual_a2.csv

⚠️ 纪律（写论文时要交代）：
  1. 判官**不能既当运动员又当裁判**：理想情况用与主模型不同的模型来判。
  2. 判官分数**必须人工校准**：人工判 20% 子集，报 Cohen's kappa；kappa < 0.6 就别用判官的数。
  3. 本脚本会打印判官用了哪个模型，论文里必须写明。
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EVAL_DIR))

import metrics as M  # noqa: E402

from eduagent import store        # noqa: E402
from eduagent.llm import get_llm  # noqa: E402

OUT_DIR = EVAL_DIR / "out"

JUDGE_SYS = (
    "你是一位严格的课程问答评审。给你【学生问题】【课程资料】【系统回答】，"
    "请按下面三步评分，最后只输出 JSON。\n"
    "第一步：把系统回答拆成若干条独立断言，逐条判断它是否能在课程资料里找到依据。\n"
    "第二步：判断回答是否正面回应了学生的问题。\n"
    "第三步：按 1-5 打分（1=完全不可用，3=基本可用但有错漏，5=完全正确且有依据）。\n"
    '只输出：{"groundedness":n,"relevance":n,"correctness":n,"unsupported_claims":["..."],"reason":"一句话"}'
)


def load_records(arm: str, tag: str = ""):
    path = OUT_DIR / ("%s%s.jsonl" % (arm, ("_" + tag) if tag else ""))
    if not path.exists():
        raise SystemExit("找不到 %s，先跑 run_eval.py" % path)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()], path


def context_of(rec) -> str:
    """取该题真实喂给模型的资料（从会话消息里捞，不重新检索，保证是当时看到的）。"""
    sid = rec.get("session_id")
    if not sid:
        return ""
    parts = []
    for msg in store.load_messages(sid):
        if msg.get("role") == "tool":
            parts.append(str(msg.get("content", "")))
    return "\n\n".join(parts)[:6000]


def ask_judge(llm, question: str, context: str, answer: str):
    user = ("【学生问题】%s\n\n【课程资料】\n%s\n\n【系统回答】\n%s\n\n请按三步评分并输出 JSON。"
            % (question, context or "（无资料）", answer or "（空回答）"))
    resp = llm.chat([{"role": "system", "content": JUDGE_SYS},
                     {"role": "user", "content": user}], [])
    raw = resp.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = {}
    return {"raw": raw[:600], "parsed": data}


def cmd_judge(args):
    recs, path = load_records(args.arm, args.tag)
    if args.sample and args.sample < len(recs):
        recs = random.sample(recs, args.sample)
    elif args.limit:
        recs = recs[: args.limit]
    llm = get_llm()
    if type(llm).__name__ == "MockLLM":
        print("⚠ 当前是 mock 模型，判官分数没有意义。请先配真模型：")
        print("   export LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=...")
        return 2
    print("判官模型：%s　待判 %d 条（每题 1 次调用）" % (llm.name, len(recs)))
    out = []
    for i, r in enumerate(recs, 1):
        ctx = context_of(r)
        verdict = ask_judge(llm, r["question"], ctx, r.get("answer", ""))
        p = verdict["parsed"]
        row = {
            "qid": r["qid"], "group": r["group"], "arm": r["arm"],
            "judge_model": llm.name,
            "groundedness": p.get("groundedness"), "relevance": p.get("relevance"),
            "correctness": p.get("correctness"),
            "unsupported_claims": p.get("unsupported_claims") or [],
            "judge_reason": p.get("reason", ""),
            "auto_score": r.get("score"), "question": r["question"],
            "answer": (r.get("answer") or "")[:400],
        }
        out.append(row)
        print("  %2d/%d %-6s grounded=%s relevance=%s correct=%s"
              % (i, len(recs), r["qid"], row["groundedness"], row["relevance"], row["correctness"]))
    dest = OUT_DIR / ("judged_%s%s.jsonl" % (args.arm, ("_" + args.tag) if args.tag else ""))
    dest.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out), encoding="utf-8")

    def avg(k):
        vs = [x[k] for x in out if isinstance(x.get(k), (int, float))]
        return round(sum(vs) / len(vs), 3) if vs else None

    print("\n判官均分（1–5）：groundedness=%s　relevance=%s　correctness=%s"
          % (avg("groundedness"), avg("relevance"), avg("correctness")))
    unsupported = sum(1 for x in out if x["unsupported_claims"])
    print("含无依据断言的题：%d/%d" % (unsupported, len(out)))
    print("→ %s" % dest)
    print("提醒：判官分数必须人工校准后才能进论文（--kappa）。")
    return 0


def cmd_kappa(args):
    """判官（或两名人评）与人工标注的一致度，报 Cohen's kappa。"""
    judged = [json.loads(l) for l in Path(args.judged).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for l in Path(args.human).read_text(encoding="utf-8-sig").splitlines():
        if l.strip():
            rows.append(next(iter(__import__("csv").DictReader([l]))))
    human = {r["qid"]: r for r in rows if r.get("qid")}
    a, b, pairs = [], [], []
    for j in judged:
        h = human.get(j["qid"])
        if not h:
            continue
        hv = (h.get("human_score") or "").strip()
        if not hv:
            continue
        a.append(str(j.get("correctness")))
        b.append(hv)
        pairs.append((j["qid"], j.get("correctness"), hv, j.get("auto_score")))
    if not a:
        print("没有可比对的行：判官文件里的 qid 要在人工表里，且 human_score 要填好。")
        return 1
    k = M.cohens_kappa(a, b)
    agree = sum(1 for x, y in zip(a, b) if x == y) / len(a)
    print("可比对 %d 条　判官-人工 一致率 %.1f%%　Cohen's kappa = %.3f"
          % (len(a), agree * 100, k))
    print("判读：kappa>0.8 很好，0.6-0.8 可用，<0.6 判官不可用于论文结论。")
    for q, jv, hv, av in pairs:
        flag = "" if str(jv) == hv else "  ← 不一致"
        print("  %-6s 判官=%s 人工=%s 自动=%s%s" % (q, jv, hv, av, flag))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 判官（第 3 层语义指标）")
    ap.add_argument("--arm", default="a2")
    ap.add_argument("--tag", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sample", type=int, default=0, help="随机抽 N 条")
    ap.add_argument("--kappa", nargs=2, metavar=("JUDGED", "HUMAN"), help="算判官与人工的一致度")
    ap.add_argument("--judged", default="")
    ap.add_argument("--human", default="")
    args = ap.parse_args()
    if args.kappa:
        args.judged, args.human = args.kappa
        return cmd_kappa(args)
    return cmd_judge(args)


if __name__ == "__main__":
    sys.exit(main())
