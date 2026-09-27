# -*- coding: utf-8 -*-
"""按当前判分规则**重算**已有结果的分数（不重跑模型）。

为什么需要它：判分规则（关键词、引用口径、拒答词表）会不断收紧，
但答案本身没变 —— 重跑一遍模型既慢又不可比（模型有随机性）。
本脚本只重新应用规则，**模型输出一字不动**，所以两次结果可比。

用法：
    python eval/rescore.py                 # 处理 out/ 下所有 jsonl（就地更新）
    python eval/rescore.py --tag qwen3b    # 只处理该批次
    python eval/rescore.py --dry-run       # 只打印变化，不写回
"""
import argparse
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR.parent))

import metrics as M  # noqa: E402

OUT_DIR = EVAL_DIR / "out"


def rescore_file(path: Path, qmap: dict, dry: bool = False):
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    before = [r.get("score") for r in recs]
    changed = []
    for r in recs:
        item = qmap.get(r["qid"])
        if not item:
            continue
        answer = r.get("answer", "")
        retrieved = r.get("retrieved_ids") or []
        v = M.judge_auto(item, answer, retrieved)
        new = {
            "score": v["score"], "pass": v["pass"], "refused": v.get("refused", False),
            "cited": v.get("cited", []), "cited_gold": v.get("cited_gold", False),
            "cite_valid": v.get("cite_valid", False), "reasons": v.get("reasons", []),
        }
        if not v.get("cited"):
            new["cite_valid"] = False
        new.update(M.retrieval_metrics(retrieved, r.get("gold_ids") or []))
        new["point_lexical_recall"] = M.point_lexical_recall(item, answer)
        if abs((r.get("score") or 0) - new["score"]) > 1e-9:
            changed.append((r["qid"], r.get("score"), new["score"]))
        r.update(new)
    after = [r.get("score") for r in recs]
    if not dry:
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs),
                        encoding="utf-8")
    n = len(recs)
    return {
        "file": path.name, "n": n,
        "before_mean": round(sum(before) / n, 4) if n else 0,
        "after_mean": round(sum(after) / n, 4) if n else 0,
        "before_pass": sum(1 for x in before if (x or 0) >= 1),
        "after_pass": sum(1 for x in after if (x or 0) >= 1),
        "changed": changed,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="重算评测分数（不重跑模型）")
    ap.add_argument("--tag", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    qmap = {q["qid"]: q for q in M.load_questions()}
    files = [f for f in sorted(OUT_DIR.glob("*.jsonl"))
             if not f.name.startswith(("judged_", "sheet_"))
             and (not args.tag or f.stem.endswith(args.tag))]
    if not files:
        print("没有可处理的结果文件。")
        return 1
    print("%-28s %6s %10s %10s %8s %8s" % ("文件", "条数", "原均分", "新均分", "原满分", "新满分"))
    for f in files:
        s = rescore_file(f, qmap, dry=args.dry_run)
        flag = "  ← 有变化" if s["changed"] else ""
        print("%-28s %6d %10.3f %10.3f %8d %8d%s"
              % (s["file"], s["n"], s["before_mean"], s["after_mean"],
                 s["before_pass"], s["after_pass"], flag))
        for qid, b, a in s["changed"][:6]:
            print("      %-6s %.1f → %.1f" % (qid, b, a))
    if args.dry_run:
        print("\n（--dry-run：没有写回文件）")
    else:
        print("\n已就地更新。接着跑：python eval/report.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
