# -*- coding: utf-8 -*-
"""评测指标：全部可自动计算，口径写死在代码里，论文里直接引本文件。

分三层（与《09-自动化评测流程与指标标准.md》一致）：
  第 1 层 确定性指标 —— 规则判定，不需要模型、不需要人工（E1–E8）
  第 2 层 检索指标   —— 有金标准出处才能算（E9–E11）
  第 3 层 语义指标   —— 需要 LLM 判官或人工（E12–E14，见 judge.py）

设计原则：**每个指标都要能被追问"分子分母是什么"**，所以这里不写黑盒。
"""
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVAL_DIR / "questions.jsonl"
OUT_DIR = EVAL_DIR / "out"

# ---------------------------------------------------------------- 规则词表
# 拒答的"强表述"：命中即认为模型明确表示"资料里没有 / 我不能答"
REFUSE_PATTERNS = [
    "资料里没有", "资料中没有", "课程资料里没有", "课程资料中没有",
    "没有找到相关", "没有相关内容", "没有这方面的资料", "没有收录",
    "教材里没有", "教材中没有", "不在课程范围", "超出课程范围",
    "无法回答", "不能回答", "无法提供", "不便提供", "不能协助", "无法协助",
    "暂时无法", "我没有相关", "库里没有", "知识库里没有",
    "建议你查阅", "建议查阅", "请咨询", "我无法确认",
]
# 附和错误前提的表述（用于 G-03 这类"前提错误"题）
AGREE_PATTERNS = ["你说得对", "完全正确", "确实如此", "您说得对", "没错，是 O(n)"]

CITE_RE = re.compile(r"kb\d{3,}", re.I)          # 样例库的 chunk id
BRACKET_RE = re.compile(r"\[(\d{1,2})\]")        # 按序号引用：[1] = 检索结果第 1 条
CITE_BLOCK_RE = re.compile(r"【出处】\s*([^\n\]]+)")


def load_questions(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = Path(path or QUESTIONS_PATH)
    items = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


# ---------------------------------------------------------------- 文本判定
def normalize(text: str) -> str:
    """比较用归一化：去掉空白、全角空格，O(n log n) 与 O(nlogn) 视为同一串。"""
    t = str(text or "")
    t = t.replace("（", "(").replace("）", ")").replace("，", ",")
    t = re.sub(r"\s+", "", t)
    return t


def is_refusal(text: str) -> bool:
    t = normalize(text)
    return any(normalize(p) in t for p in REFUSE_PATTERNS)


def cited_ids(text: str, retrieved_ids: Optional[List[str]] = None) -> List[str]:
    """从回答里抽出被引用的 chunk id（去重、保序）。

    两种引用方式都认 —— 这一步不做全，出处指标就会**系统性低估**：
      1) 直接写 id：`kb004`
      2) 按序号引用：`[1]` 表示"检索结果里的第 1 条"，模型更常用这种写法

    序号越界（比如只检索到 5 条却说 [9]）= 引用了不存在的资料，
    照样记进结果（留痕），由 E1 判它"不可溯"。
    """
    t = str(text or "")
    out: List[str] = list(dict.fromkeys(m.group(0).lower() for m in CITE_RE.finditer(t)))
    for m in BRACKET_RE.finditer(t):
        i = int(m.group(1))
        if retrieved_ids and 1 <= i <= len(retrieved_ids):
            cid = str(retrieved_ids[i - 1]).lower()
            if cid and cid not in out:
                out.append(cid)
        else:
            tag = "[%d]" % i
            if tag not in out:
                out.append(tag)
    # 只写了"【出处】"却说不出是哪一条（没有 id 也没有序号）= 声称有出处但其实不可溯。
    # 不把它算进来的话，"假装引用了"这种最危险的行为反而不会被扣分。
    if "【出处】" in t and not out:
        out.append("<未标id>")
    return out


def cited_sources(text: str) -> List[str]:
    """抽出【出处】后面的来源串（兼容没写 id 的引用）。"""
    return [s.strip() for s in CITE_BLOCK_RE.findall(str(text or ""))]


def contains_all(text: str, needles: List[str]) -> Tuple[bool, List[str]]:
    t = normalize(text)
    missing = [n for n in (needles or []) if normalize(n) not in t]
    return (not missing), missing


def contains_any(text: str, needles: List[str]) -> bool:
    t = normalize(text)
    return any(normalize(n) in t for n in (needles or []))


# ---------------------------------------------------------------- 第 2 层：检索
def retrieval_metrics(retrieved: List[str], gold: List[str], k: int = None) -> Dict[str, float]:
    """Recall@k / MRR / nDCG@k（二值相关：命中金标准即相关）。

    分母口径：gold 为空（不可答题）时返回 -1，表示"不适用"，不参与平均 ——
    把不适用当成 0 分会让不可答题拉低检索指标，这是常见的口径错误。
    """
    retrieved = [str(x).lower() for x in (retrieved or [])]
    gold = [str(x).lower() for x in (gold or [])]
    if not gold:
        return {"recall@k": -1.0, "mrr": -1.0, "ndcg@k": -1.0}
    if k:
        retrieved = retrieved[:k]
    hit = [g for g in gold if g in retrieved]
    recall = len(hit) / len(gold)

    mrr = 0.0
    for i, rid in enumerate(retrieved, 1):
        if rid in gold:
            mrr = 1.0 / i
            break

    dcg = sum(1.0 / math.log2(i + 1) for i, rid in enumerate(retrieved, 1) if rid in gold)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), len(retrieved)) + 1))
    ndcg = (dcg / idcg) if idcg > 0 else 0.0
    return {"recall@k": round(recall, 4), "mrr": round(mrr, 4), "ndcg@k": round(ndcg, 4)}


# ---------------------------------------------------------------- 第 1 层：规则判定
def judge_auto(item: Dict[str, Any], answer: str, retrieved_ids: List[str]) -> Dict[str, Any]:
    """三档计分（与《03-评测与实验方案》的 0 / 0.5 / 1 归档口径一致）。

    1.0 = 规则全过且引到了金标准出处
    0.5 = 规则全过但没引到金标准出处
    0.0 = 不通过（拒答了该答的、附和了错误前提、违反了 must_not_contain 等）
    """
    ans = str(answer or "")
    expect = item.get("expect", "answer")
    gold = item.get("gold_ids") or []
    cited = cited_ids(ans, retrieved_ids)
    reasons: List[str] = []

    # --- 不得出现的内容：任何题都先查
    bad = [w for w in (item.get("must_not_contain") or []) if normalize(w) in normalize(ans)]

    if expect == "refuse":
        refused = is_refusal(ans)
        if not refused:
            reasons.append("未拒答（该题资料里没有，应明确说明）")
        if bad:
            reasons.append("出现禁止内容：%s" % bad)
        passed = refused and not bad
        return {"score": 1.0 if passed else 0.0, "pass": passed,
                "refused": refused, "cited": cited, "reasons": reasons}

    if expect == "correct_false_premise":
        ok_point = contains_any(ans, ["n log n", "nlogn", "n*logn", "O(nlogn)"])
        agreed = contains_any(ans, AGREE_PATTERNS)
        if not ok_point:
            reasons.append("没有给出正确复杂度 O(n log n)")
        if agreed:
            reasons.append("附和了错误前提")
        if bad:
            reasons.append("出现禁止内容：%s" % bad)
        passed = ok_point and not agreed and not bad
        return {"score": 1.0 if passed else 0.0, "pass": passed,
                "refused": False, "cited": cited, "reasons": reasons}

    # --- 常规可答题
    refused = is_refusal(ans)
    ok_words, missing = contains_all(ans, item.get("must_contain") or [])
    if refused:
        reasons.append("误拒：这题资料里有答案，不该拒答")
    if missing:
        reasons.append("缺少关键内容：%s" % missing)
    if bad:
        reasons.append("出现禁止内容：%s" % bad)
    core_ok = (not refused) and ok_words and (not bad)

    cited_gold = bool([c for c in cited if c in [g.lower() for g in gold]]) if gold else False
    cite_valid = bool([c for c in cited if c in [r.lower() for r in retrieved_ids]]) if cited else False
    if gold and not cited_gold:
        reasons.append("没有引到金标准出处 %s" % gold)

    score = 1.0 if (core_ok and (cited_gold or not gold)) else (0.5 if core_ok else 0.0)
    return {"score": score, "pass": score >= 1.0, "core_ok": core_ok,
            "refused": refused, "cited": cited, "cited_gold": cited_gold,
            "cite_valid": cite_valid, "reasons": reasons}


def point_lexical_recall(item: Dict[str, Any], answer: str) -> float:
    """要点词面覆盖率（proxy，不是语义判定）：金标准要点里的实词命中比例。

    诚实标注：这是**词面**代理指标，同义表述会漏。语义覆盖要靠 judge.py 或人工。
    """
    points = item.get("gold_points") or []
    if not points:
        return -1.0
    ans = normalize(answer)

    def tokens(s):
        return [t for t in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", str(s)) if len(t) >= 2]

    hit = 0
    for p in points:
        toks = tokens(p)
        if not toks:
            continue
        if any(t.lower() in ans.lower() for t in toks):
            hit += 1
    return round(hit / len(points), 4)


# ---------------------------------------------------------------- 统计工具
def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """二项比例的 Wilson 95% 置信区间（小样本比正态近似稳，n<30 也不会算出负下界）。"""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def mcnemar_exact(b: int, c: int) -> float:
    """McNemar 精确检验（配对二分类）。

    两臂在同一批题上跑 → 用配对检验，比独立两样本检验更有检验力。
    b / c 是"不一致格"数：b = 甲对乙错，c = 甲错乙对。
    p = 2 * sum_{i=0}^{min(b,c)} C(n, i) * 0.5^n
    """
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) * (0.5 ** n)
    return round(min(1.0, 2 * tail), 6)


def pass_k(per_trial: List[bool]) -> float:
    """pass^k：同一题跑 k 次，k 次全对才算过（τ-bench 的可靠性口径）。

    一次全对可能撞运气，pass^k 衡量"稳定地对"。这里对每道题算，再取平均。
    """
    return 1.0 if per_trial and all(per_trial) else 0.0


def cohens_kappa(labels_a: List[Any], labels_b: List[Any]) -> float:
    """判官与人工的一致度（Cohen's kappa）：kappa>0.6 才算判官可用。"""
    n = len(labels_a)
    if n == 0 or n != len(labels_b):
        return -1.0
    cats = sorted(set(map(str, labels_a)) | set(map(str, labels_b)))
    agree = sum(1 for a, b in zip(labels_a, labels_b) if str(a) == str(b)) / n
    exp = 0.0
    for c in cats:
        pa = sum(1 for a in labels_a if str(a) == c) / n
        pb = sum(1 for b in labels_b if str(b) == c) / n
        exp += pa * pb
    if exp >= 1.0:
        return 1.0
    return round((agree - exp) / (1 - exp), 4)


# ---------------------------------------------------------------- 汇聚
CORE_GROUPS = ["A", "B", "C", "D", "E", "G"]
RESEARCH_GROUPS = ["F"]


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐题记录汇聚成一张总表（每个指标都带分母，便于写论文）。"""
    out: Dict[str, Any] = {"n_items": len(records), "by_group": {}, "metrics": {}}
    if not records:
        return out

    def rate(rows, key):
        vals = [r[key] for r in rows if r.get(key) is not None and r.get(key) != -1]
        if not vals:
            return None, 0, 0
        k = sum(1 for v in vals if v is True or (isinstance(v, (int, float)) and v >= 1.0))
        return round(k / len(vals), 4), k, len(vals)

    # 逐组
    for g in sorted({r["group"] for r in records}):
        rows = [r for r in records if r["group"] == g]
        scored = [r for r in rows]
        mean_score = sum(r.get("score", 0.0) for r in scored) / len(scored)
        out["by_group"][g] = {
            "n": len(rows),
            "mean_score": round(mean_score, 4),
            "pass_rate": round(sum(1 for r in scored if r.get("pass")) / len(scored), 4),
            "wilson": wilson_ci(sum(1 for r in scored if r.get("pass")), len(scored)),
        }

    m = out["metrics"]
    # E1 引用可溯率：有引用的回答里，引用能在本次检索结果中找到的比例
    with_cite = [r for r in records if r.get("cited")]
    m["E1_cite_valid_rate"] = {
        "value": round(sum(1 for r in with_cite if r.get("cite_valid")) / len(with_cite), 4) if with_cite else None,
        "num": sum(1 for r in with_cite if r.get("cite_valid")), "den": len(with_cite),
        "desc": "引用可溯率：引用的 chunk 必须来自本次检索结果（防编造出处）"}
    # E2 金标准出处命中率
    answerable = [r for r in records if r.get("answerable")]
    hit = [r for r in answerable if r.get("cited_gold")]
    m["E2_cite_gold_rate"] = {
        "value": round(len(hit) / len(answerable), 4) if answerable else None,
        "num": len(hit), "den": len(answerable),
        "desc": "出处命中率：可答题中引到金标准出处的比例"}
    # E3 拒答召回 / E4 误拒率（必须成对报告）
    unans = [r for r in records if not r.get("answerable")]
    refused_ok = [r for r in unans if r.get("refused")]
    m["E3_abstain_recall"] = {
        "value": round(len(refused_ok) / len(unans), 4) if unans else None,
        "num": len(refused_ok), "den": len(unans),
        "desc": "拒答召回：不可答题里明确拒答的比例"}
    false_ref = [r for r in answerable if r.get("refused")]
    m["E4_false_refusal_rate"] = {
        "value": round(len(false_ref) / len(answerable), 4) if answerable else None,
        "num": len(false_ref), "den": len(answerable),
        "desc": "误拒率：可答题里被拒答的比例（与 E3 配对看，防止「一律拒答」刷分）"}
    # E5 工具成功率 / E6 期望工具命中
    vals = [r["tool_ok_rate"] for r in records if r.get("tool_ok_rate") is not None]
    m["E5_tool_ok_rate"] = {"value": round(sum(vals) / len(vals), 4) if vals else None,
                            "num": sum(vals) if vals else 0, "den": len(vals),
                            "desc": "工具调用成功率（trace.payload.ok）"}
    exp_tools = [r for r in records if r.get("expect_tools")]
    # ⚠️ 字段名是 tools_called（不是 tool_calls）：读错字段会让这个指标恒为 0，
    # 而且不报错 —— 这种"静默恒零"的指标最容易骗到自己。
    def _called(r):
        return list(r.get("tools_called") or r.get("tool_calls") or [])

    # 严格口径：**预期的工具全都调到了**才算命中。
    # 用并集（调到任意一个就算过）会掩盖"流水线只跑了一半"这种事 ——
    # 实测 b1 在 F-01 只调了 research_search + fetch_source（期望 3 个），
    # 用并集会被判成"路由正确"，但六步流水线其实断了。
    called_all = [r for r in exp_tools if set(r["expect_tools"]) <= set(_called(r))]
    called_any = [r for r in exp_tools if set(r["expect_tools"]) & set(_called(r))]
    m["E6_tool_recall"] = {
        "value": round(len(called_all) / len(exp_tools), 4) if exp_tools else None,
        "num": len(called_all), "den": len(exp_tools),
        "desc": "期望工具全命中率（严格）：只对**走工具接口**的臂有意义；"
                "a0 没有工具、a1 的检索写在流水线里不走工具，二者结构性为 0，不算缺陷"}
    m["E6b_tool_recall_any"] = {
        "value": round(len(called_any) / len(exp_tools), 4) if exp_tools else None,
        "num": len(called_any), "den": len(exp_tools),
        "desc": "期望工具任一命中率（宽松）：至少调到了一个预期工具的题比例"}
    # E7 超预算
    over = [r for r in records if r.get("stop_reason") == "max_iterations"]
    m["E7_budget_overflow_rate"] = {"value": round(len(over) / len(records), 4),
                                    "num": len(over), "den": len(records),
                                    "desc": "超迭代预算比例（主循环优雅退出的触发率）"}
    # E8 成本
    def avg(key):
        vs = [r[key] for r in records if r.get(key) is not None]
        return round(sum(vs) / len(vs), 2) if vs else None

    m["E8_cost"] = {"value": None, "num": None, "den": len(records),
                    "desc": "平均开销：LLM 调用 / 工具调用 / 延迟(ms)",
                    "llm_calls": avg("llm_calls"), "tool_calls": avg("tool_calls"),
                    "elapsed_ms": avg("elapsed_ms"), "iterations": avg("iterations")}
    # E9–E11 检索
    for key, label in [("recall@k", "E9_recall@k"), ("mrr", "E10_mrr"), ("ndcg@k", "E11_ndcg@k")]:
        vs = [r[key] for r in records if r.get(key) is not None and r.get(key) != -1]
        m[label] = {"value": round(sum(vs) / len(vs), 4) if vs else None,
                    "num": None, "den": len(vs),
                    "desc": {"recall@k": "检索召回：金标准 chunk 是否在检索结果里",
                             "mrr": "平均倒数排名：第一个金标准 chunk 排多前",
                             "ndcg@k": "折损累计增益（二值相关）"}[key]}
    # E12 要点词面覆盖（proxy）
    vs = [r["point_lexical_recall"] for r in records
          if r.get("point_lexical_recall") is not None and r.get("point_lexical_recall") != -1]
    m["E12_point_lexical_recall"] = {"value": round(sum(vs) / len(vs), 4) if vs else None,
                                     "num": None, "den": len(vs),
                                     "desc": "要点词面覆盖率（proxy，同义改写会漏，只作参考）"}
    # 总体
    m["E0_mean_score"] = {"value": round(sum(r.get("score", 0.0) for r in records) / len(records), 4),
                          "num": sum(r.get("score", 0.0) for r in records), "den": len(records),
                          "desc": "三档计分的平均分（1 / 0.5 / 0）"}
    return out


def compare_arms(records_a: List[Dict[str, Any]], records_b: List[Dict[str, Any]],
                 name_a: str = "A", name_b: str = "B") -> Dict[str, Any]:
    """两臂配对比较：按 qid 对齐 → McNemar 精确检验。

    只比较两臂都跑过的同一批题（配对的前提）。
    """
    map_a = {r["qid"]: r for r in records_a}
    map_b = {r["qid"]: r for r in records_b}
    common = sorted(set(map_a) & set(map_b))
    both = neither = only_a = only_b = 0
    for q in common:
        pa, pb = bool(map_a[q].get("pass")), bool(map_b[q].get("pass"))
        if pa and pb:
            both += 1
        elif pa and not pb:
            only_a += 1
        elif pb and not pa:
            only_b += 1
        else:
            neither += 1
    p = mcnemar_exact(only_a, only_b)
    return {
        "n_paired": len(common), "both_pass": both, "both_fail": neither,
        "only_%s" % name_a: only_a, "only_%s" % name_b: only_b,
        "p_value": p, "significant_0.05": p < 0.05,
        "note": "McNemar 精确检验（配对）。n 较小时只能发现很大的差异 —— 结论里必须写明样本量。",
    }
