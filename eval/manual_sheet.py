# -*- coding: utf-8 -*-
"""人工判分表：把自动判不了的东西（答案对不对、讲得好不好）交给人。

为什么必须有这一步：本项目的规则判定只能保证"出处没编造"，不能保证"答案内容正确"。
论文里的 M1 答案正确率必须是**人工双盲判**的，自动化只负责省掉整理表格的功夫。

用法：
    python eval/manual_sheet.py --arm a2 --tag qwen3b        # 生成待判分表
    python eval/manual_sheet.py --merge out/sheet1.csv out/sheet2.csv   # 两人独立判 → 一致度
    python eval/manual_sheet.py --stats out/sheet_a2.csv     # 评分完成后的汇总
"""
import argparse
import csv
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR.parent))

import metrics as M  # noqa: E402

OUT_DIR = EVAL_DIR / "out"
FIELDS = ["qid", "group", "category", "question", "gold_points", "retrieved_ids",
          "auto_score", "auto_reasons", "answer",
          "human_score", "human_note", "human2_score", "human2_note"]

HEADER_NOTE = (
    "# 人工判分说明（读完删掉本行即可）\n"
    "# human_score 填 1 / 0.5 / 0：1=要点全部答到且无错误；0.5=答到一半或有小错；0=答错或答非所问（拒答了该答的也算 0）\n"
    "# 不可答题（group D）反过来：明确说资料里没有=1，硬编=0\n"
    "# human2_score 由第二位同学独立判（不要看第一位的分数）\n"
    "# 判完跑：python eval/manual_sheet.py --merge <sheet1> <sheet2>   然后 --stats\n"
)


def load_records(arm: str, tag: str):
    path = OUT_DIR / ("%s%s.jsonl" % (arm, ("_" + tag) if tag else ""))
    if not path.exists():
        raise SystemExit("找不到 %s，先跑 run_eval.py" % path)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def make_sheet(args):
    recs = load_records(args.arm, args.tag)
    qmap = {q["qid"]: q for q in M.load_questions()}
    dest = OUT_DIR / ("sheet_%s%s.csv" % (args.arm, ("_" + args.tag) if args.tag else ""))
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        f.write(HEADER_NOTE)
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in recs:
            q = qmap.get(r["qid"], {})
            w.writerow({
                "qid": r["qid"], "group": r["group"], "category": r.get("category", ""),
                "question": r["question"],
                "gold_points": " | ".join(q.get("gold_points") or []),
                "retrieved_ids": ",".join(r.get("retrieved_ids") or []),
                "auto_score": r.get("score"),
                "auto_reasons": "；".join(r.get("reasons") or []),
                "answer": (r.get("answer") or "").replace("\n", " ")[:600],
                "human_score": "", "human_note": "", "human2_score": "", "human2_note": "",
            })
    print("已生成判分表：%s（%d 条）" % (dest, len(recs)))
    print("判完两位同学各一份后：python eval/manual_sheet.py --merge <a.csv> <b.csv>")
    return 0


def read_sheet(path: Path):
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for line in f:
            if line.startswith("#"):
                continue
            rows.append(line)
    return list(csv.DictReader(rows))


def merge(a_path, b_path):
    a = {r["qid"]: r for r in read_sheet(Path(a_path)) if r.get("qid")}
    b = {r["qid"]: r for r in read_sheet(Path(b_path)) if r.get("qid")}
    common = [q for q in a if q in b]
    la = [a[q]["human_score"] for q in common if a[q].get("human_score")]
    lb = [b[q]["human2_score"] or b[q]["human_score"] for q in common if a[q].get("human_score")]
    if not la:
        print("两份表里 human_score 都还是空的，先判分。")
        return 1
    k = M.cohens_kappa(la, lb)
    agree = sum(1 for x, y in zip(la, lb) if x == y) / len(la)
    print("两人独立判 %d 条：一致率 %.1f%%，Cohen's kappa = %.3f" % (len(la), agree * 100, k))
    print("判读：kappa>0.8 很好，0.6-0.8 可用，<0.6 说明判分口径要先对齐（讨论一次再重判）。")
    for q in common:
        x, y = a[q].get("human_score"), (b[q].get("human2_score") or b[q].get("human_score"))
        if x != y:
            print("  分歧 %-6s 甲=%s 乙=%s　（留给第三人裁）" % (q, x, y))
    return 0


def stats(path):
    rows = read_sheet(Path(path))
    scored = [r for r in rows if (r.get("human_score") or "").strip()]
    if not scored:
        print("还没有人填 human_score。")
        return 1

    def num(x):
        try:
            return float(x)
        except Exception:
            return None

    vals = [num(r["human_score"]) for r in scored]
    vals = [v for v in vals if v is not None]
    print("人工判分 %d 条（另有 %d 条未判）" % (len(vals), len(rows) - len(scored)))
    print("  M1 人工答案正确率（均分）：%.3f" % (sum(vals) / len(vals)))
    print("  满分率 %.1f%%　半数分率 %.1f%%　零分率 %.1f%%"
          % (sum(1 for v in vals if v >= 1) / len(vals) * 100,
             sum(1 for v in vals if v == 0.5) / len(vals) * 100,
             sum(1 for v in vals if v == 0) / len(vals) * 100))
    # 自动判 vs 人工判 的一致度：自动判到底靠不靠谱，用这个数说话
    pairs = [(num(r.get("auto_score")), num(r["human_score"])) for r in scored]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if pairs:
        agree = sum(1 for a, b in pairs if abs(a - b) < 1e-6) / len(pairs)
        loose = sum(1 for a, b in pairs if a >= 0.5 and b >= 0.5) / len(pairs)
        k = M.cohens_kappa([str(a) for a, _ in pairs], [str(b) for _, b in pairs])
        print("  自动判 vs 人工判：严格一致 %.1f%%，「是否可用」一致 %.1f%%，kappa=%.3f"
              % (agree * 100, loose * 100, k))
        print("  → 论文里要报这一行：它说明规则判定能替代多少人工。")
    by_group = {}
    for r in scored:
        v = num(r["human_score"])
        if v is not None:
            by_group.setdefault(r["group"], []).append(v)
    print("  分组均分：" + "　".join("%s=%.2f(n=%d)" % (g, sum(v) / len(v), len(v))
                                    for g, v in sorted(by_group.items())))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="人工判分表工具")
    ap.add_argument("--arm", default="a2")
    ap.add_argument("--tag", default="")
    ap.add_argument("--merge", nargs=2, metavar=("A", "B"))
    ap.add_argument("--stats", default="")
    args = ap.parse_args()
    if args.merge:
        return merge(args.merge[0], args.merge[1])
    if args.stats:
        return stats(args.stats)
    return make_sheet(args)


if __name__ == "__main__":
    sys.exit(main())
