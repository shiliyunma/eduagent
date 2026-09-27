# -*- coding: utf-8 -*-
"""指标自检：拿**构造好的输入**断言指标输出，防止再出现"静默恒零"的指标。

为什么必须要有这个文件：E6 曾经因为读错字段名（读 `tool_calls`，实际是 `tools_called`）
**恒为 0 且不报任何错** —— 看起来像"系统完全不会路由"，实际只是读错了 key。
这类 bug 只能靠"给已知输入、断言已知输出"发现，靠跑真数据是发现不了的。

用法：
    python eval/selftest_metrics.py      # 退出码 0 = 全过
"""
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import metrics as M  # noqa: E402

FAILS = []
N = [0]


def check(name, got, want):
    N[0] += 1
    # 指标值在 metrics.py 里统一四舍五入到 4 位，所以容差取 1e-3（够松，能容纳舍入；
    # 够紧，真错了比如 0.333 vs 0.5 一定会被抓到）
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        ok = isinstance(got, (int, float)) and abs(got - want) < 1e-3
    else:
        ok = (got == want)
    print("  [%s] %-46s got=%r want=%r" % ("OK" if ok else "!!", name, got, want))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- 第 2 层：检索
print("检索指标（Recall@k / MRR / nDCG）")
r = M.retrieval_metrics(["kb004", "kb001"], ["kb004"])
check("金标准排第 1 → recall=1", r["recall@k"], 1.0)
check("金标准排第 1 → mrr=1", r["mrr"], 1.0)
check("金标准排第 1 → ndcg=1", r["ndcg@k"], 1.0)
r = M.retrieval_metrics(["kb001", "kb004"], ["kb004"])
check("金标准排第 2 → mrr=0.5", r["mrr"], 0.5)
r = M.retrieval_metrics(["kb001"], ["kb004"])
check("没检索到 → recall=0", r["recall@k"], 0.0)
r = M.retrieval_metrics(["kb001"], [])
check("不可答题（无金标准）→ 不适用 = -1", r["recall@k"], -1.0)

# ---------------------------------------------------------------- 引用解析
print("\n引用解析（这是最容易低估的地方）")
check("直接写 id", M.cited_ids("见 kb004", ["kb004"]), ["kb004"])
check("按序号引用 → 映射回 id", M.cited_ids("根据资料[1]", ["kb004", "kb001"]), ["kb004"])
check("序号越界 → 保留痕迹", M.cited_ids("根据资料[9]", ["kb004"]), ["[9]"])
check("只写【出处】没标 id → 假引用", M.cited_ids("【出处】搜索知识库得到：xxx", []), ["<未标id>"])
check("同一 id 出现多次只记一次", M.cited_ids("kb004 和 kb004", ["kb004"]), ["kb004"])

# ---------------------------------------------------------------- 三档计分
print("\n规则判分（1 / 0.5 / 0 三档）")
item = {"expect": "answer", "gold_ids": ["kb004"], "answerable": 1, "must_contain": ["B+树"]}
v = M.judge_auto(item, "B+树是多路平衡树【出处】kb004", ["kb004"])
check("内容对且引到金标准 → 1.0", v["score"], 1.0)
v = M.judge_auto(item, "B+树是多路平衡树", ["kb004"])
check("内容对但没引出处 → 0.5", v["score"], 0.5)
v = M.judge_auto(item, "不知道", ["kb004"])
check("该答却拒答 → 0", v["score"], 0.0)
v = M.judge_auto({"expect": "refuse", "gold_ids": [], "answerable": 0},
                 "课程资料里没有相关内容", [])
check("不可答且明确拒答 → 1.0", v["score"], 1.0)
v = M.judge_auto({"expect": "correct_false_premise", "gold_ids": ["kb001"], "answerable": 1},
                 "快速排序平均是 O(n log n)，不是 O(n)", [])
check("纠正错误前提 → 1.0", v["score"], 1.0)
v = M.judge_auto({"expect": "correct_false_premise", "gold_ids": ["kb001"], "answerable": 1},
                 "你说得对，是 O(n)", [])
check("附和错误前提 → 0", v["score"], 0.0)

# ---------------------------------------------------------------- 汇聚层指标
print("\n汇聚指标（聚合是否读对了字段）")


def rec(qid, group, called, expect, score=1.0, refused=False, cited=None):
    # ⚠️ 刻意只放 tools_called、不放 tool_calls —— 与 run_eval.py 写出的真实记录保持一致。
    # 如果这里多给一个 tool_calls，就测不出"聚合层读错字段名"这个 bug 了。
    return {"qid": qid, "group": group, "tools_called": called, "expect_tools": expect,
            "score": score, "pass": score >= 1.0, "refused": refused,
            "cited": cited or [], "cite_valid": bool(cited), "cited_gold": bool(cited),
            "answerable": 1, "stop_reason": "stop", "elapsed_ms": 100.0,
            "llm_calls": 1, "n_tool_calls": len(called), "iterations": 1,
            "retrieved_ids": ["kb004"], "gold_ids": ["kb004"], "recall@k": 1.0,
            "mrr": 1.0, "ndcg@k": 1.0, "point_lexical_recall": 1.0, "tool_ok_rate": 1.0}


recs = [
    rec("A-01", "A", ["search_course_kb"], ["search_course_kb"]),          # 全命中
    rec("A-02", "A", [], ["search_course_kb"]),                            # 一个都没调
    rec("F-01", "F", ["research_search", "fetch_source"],
        ["research_search", "grade_sources", "plan_learning_route"]),      # 只跑了一半
]
agg = M.aggregate(recs)
check("E6 严格全命中（1/3）", agg["metrics"]["E6_tool_recall"]["value"], 1 / 3)
check("E6b 任一命中（2/3）", agg["metrics"]["E6b_tool_recall_any"]["value"], 2 / 3)
check("E6 分子", agg["metrics"]["E6_tool_recall"]["num"], 1)
check("E6 分母", agg["metrics"]["E6_tool_recall"]["den"], 3)
check("E5 工具成功率取平均", agg["metrics"]["E5_tool_ok_rate"]["value"], 1.0)
check("E0 均分", agg["metrics"]["E0_mean_score"]["value"], 1.0)
check("拒答召回（无不可答题 → None）", agg["metrics"]["E3_abstain_recall"]["value"], None)

# ---------------------------------------------------------------- 统计
print("\n统计工具")
lo, hi = M.wilson_ci(0, 10)
check("Wilson：0/10 的下界不为负", lo >= 0.0, True)
lo, hi = M.wilson_ci(10, 10)
check("Wilson：10/10 的上界不超过 1", hi <= 1.0, True)
check("McNemar 精确检验（b=12,c=2 → 0.0129）", M.mcnemar_exact(12, 2), 0.0129)
check("McNemar：无分歧 → p=1", M.mcnemar_exact(0, 0), 1.0)
check("kappa：完全一致 → 1.0", M.cohens_kappa([1, 0, 1], [1, 0, 1]), 1.0)
check("pass^k：三次全对才算稳", M.pass_k([True, True, False]), 0.0)

print("\n" + "=" * 62)
if FAILS:
    print("失败 %d / %d 项：%s" % (len(FAILS), N[0], "、".join(FAILS)))
    sys.exit(1)
print("指标自检全部通过（%d 项）。" % N[0])
print("改完 metrics.py 请先跑这个，再跑真数据 —— 别让指标静默变错。")
