# -*- coding: utf-8 -*-
"""把评测结果画成可视化仪表盘（自包含 HTML + Markdown 摘要）。

用法：
    python eval/report.py                    # 读 eval/out/ 下所有 *.jsonl
    python eval/report.py --tag qwen3b       # 只看带该后缀的文件
    python eval/report.py --open             # 生成后自动用浏览器打开

为什么不用 matplotlib：本机没装，而且零依赖的 HTML 才能随手发给导师 / 嵌进答辩演示。
图表全部是内联 SVG，单文件、离线可看、可直接打印成 PDF 塞进论文附录。
"""
import argparse
import html
import json
import sys
import time
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR.parent))

import metrics as M  # noqa: E402

OUT_DIR = EVAL_DIR / "out"
GROUP_NAMES = {
    "A": "A 单跳事实", "B": "B 多跳对比", "C": "C 多轮指代", "D": "D 不可答（拒答）",
    "E": "E 改写鲁棒", "F": "F 调研通道", "G": "G 边界对抗",
}
ARM_ORDER = ["a0", "a1", "a2", "b0", "b1"]
PALETTE = ["#6b7280", "#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa"]


# ---------------------------------------------------------------- 读数据
def load_arms(tag: str = "") -> dict:
    arms = {}
    for f in sorted(OUT_DIR.glob("*.jsonl")):
        name = f.stem
        if tag and not name.endswith(tag):
            continue
        arm = name.split("_")[0]
        recs = []
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
        if recs:
            arms.setdefault(arm, []).extend(recs)
    return arms


def arm_label(arm: str, recs) -> str:
    return (recs[0].get("arm_name") or arm) if recs else arm


# ---------------------------------------------------------------- SVG 组件
def _esc(s):
    return html.escape(str(s))


def svg_grouped_passrate(arms, agg_by_arm, width=760, height=300):
    """分组通过率：每个组一条横棒，组内按臂并排；误差棒是 Wilson 95% CI。"""
    groups = sorted({g for a in arms for g in agg_by_arm[a]["by_group"]})
    if not groups:
        return ""
    rowh = 34
    height = 40 + rowh * len(groups)
    left, right = 110, 60
    plot_w = width - left - right
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">']
    # 刻度
    for t in range(0, 6):
        x = left + plot_w * t / 5
        parts.append(f'<line x1="{x:.1f}" y1="20" x2="{x:.1f}" y2="{height-18}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="14" class="axis" text-anchor="middle">{t*20}%</text>')
    for gi, g in enumerate(groups):
        y0 = 28 + gi * rowh
        parts.append(f'<text x="{left-10}" y="{y0+16}" class="lbl" text-anchor="end">{_esc(GROUP_NAMES.get(g, g))}</text>')
        bar_h = 8
        for ai, a in enumerate(arms):
            b = agg_by_arm[a]["by_group"].get(g)
            if not b:
                continue
            v = b["pass_rate"]
            y = y0 + 2 + ai * (bar_h + 1.5)
            w = max(1.0, plot_w * v)
            col = PALETTE[ai % len(PALETTE)]
            parts.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" fill="{col}" rx="2"/>')
            lo, hi = b["wilson"]
            x1 = left + plot_w * lo
            x2 = left + plot_w * hi
            parts.append(f'<line x1="{x1:.1f}" y1="{y+bar_h/2}" x2="{x2:.1f}" y2="{y+bar_h/2}" class="whisker"/>')
            parts.append(f'<text x="{w+left+6:.1f}" y="{y+bar_h-1}" class="val">{v*100:.0f}% (n={b["n"]})</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_radar(arms, agg_by_arm, size=380):
    """雷达图：7 个归一化到 0–1 的维度，一眼看出各臂的长短板。"""
    axes = [
        ("均分", lambda m: m["E0_mean_score"]["value"] or 0),
        ("引用可溯", lambda m: m["E1_cite_valid_rate"]["value"] if m["E1_cite_valid_rate"]["value"] is not None else 0),
        ("出处命中", lambda m: m["E2_cite_gold_rate"]["value"] if m["E2_cite_gold_rate"]["value"] is not None else 0),
        ("拒答召回", lambda m: m["E3_abstain_recall"]["value"] if m["E3_abstain_recall"]["value"] is not None else 0),
        ("不误拒", lambda m: 1 - (m["E4_false_refusal_rate"]["value"] or 0)),
        ("工具成功", lambda m: m["E5_tool_ok_rate"]["value"] or 0),
        ("工具路由", lambda m: m["E6_tool_recall"]["value"] if m["E6_tool_recall"]["value"] is not None else 0),
    ]
    cx, cy, r = size / 2, size / 2 + 6, size * 0.33
    import math
    parts = [f'<svg viewBox="0 0 {size} {size+30}" width="100%" height="{size+30}">']
    n = len(axes)
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = []
        for i in range(n):
            ang = -math.pi / 2 + 2 * math.pi * i / n
            pts.append("%.1f,%.1f" % (cx + r * ring * math.cos(ang), cy + r * ring * math.sin(ang)))
        parts.append(f'<polygon points="{" ".join(pts)}" class="ring"/>')
    for i, (label, _f) in enumerate(axes):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" class="ring"/>')
        lx, ly = cx + (r + 22) * math.cos(ang), cy + (r + 22) * math.sin(ang)
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="axis" text-anchor="middle">{_esc(label)}</text>')
    for ai, a in enumerate(arms):
        m = agg_by_arm[a]["metrics"]
        pts = []
        for i, (_l, f) in enumerate(axes):
            v = max(0.0, min(1.0, float(f(m) or 0)))
            ang = -math.pi / 2 + 2 * math.pi * i / n
            pts.append("%.1f,%.1f" % (cx + r * v * math.cos(ang), cy + r * v * math.sin(ang)))
        col = PALETTE[ai % len(PALETTE)]
        parts.append(f'<polygon points="{" ".join(pts)}" fill="{col}" fill-opacity="0.16" stroke="{col}" stroke-width="2"/>')
    parts.append("</svg>")
    return "".join(parts)


def svg_scatter(arms, agg_by_arm, width=420, height=280):
    """成本-质量散点：x = 平均延迟(s)，y = 均分；气泡大小 = 平均工具调用次数。"""
    pts = []
    for a in arms:
        m = agg_by_arm[a]["metrics"]
        cost = m["E8_cost"]
        x = (cost.get("elapsed_ms") or 0) / 1000.0
        y = m["E0_mean_score"]["value"] or 0
        b = (cost.get("tool_calls") or 0)
        pts.append((a, x, y, b))
    if not pts:
        return ""
    maxx = max([p[1] for p in pts] + [1.0])
    left, right, top, bottom = 52, 24, 22, 40
    pw, ph = width - left - right, height - top - bottom
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">']
    for t in range(0, 5):
        y = top + ph * t / 4
        v = 1 - t / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+pw}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left-6}" y="{y+4:.1f}" class="axis" text-anchor="end">{v:.2f}</text>')
    for t in range(0, 5):
        x = left + pw * t / 4
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top+ph}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="{top+ph+16}" class="axis" text-anchor="middle">{maxx*t/4:.1f}</text>')
    parts.append(f'<text x="{left+pw/2:.0f}" y="{height-4}" class="axis" text-anchor="middle">平均每题延迟（秒）</text>')
    for i, (a, x, y, b) in enumerate(pts):
        px = left + pw * (x / maxx if maxx else 0)
        py = top + ph * (1 - y)
        rad = 7 + min(14, b * 2)
        col = PALETTE[i % len(PALETTE)]
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{rad:.1f}" fill="{col}" fill-opacity="0.55" stroke="{col}"/>')
        parts.append(f'<text x="{px:.1f}" y="{py-rad-4:.1f}" class="val" text-anchor="middle">{_esc(a)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_heatmap(records, arms, focus=None, width=None):
    """逐题热力图：每行一道题，每列一条臂；绿=满分、黄=半分、红=0分、灰=该臂没跑这题。"""
    qids = []
    for a in arms:
        for r in records[a]:
            if r["qid"] not in qids:
                qids.append(r["qid"])
    qids.sort()
    focus = focus or arms[-1]
    cell, gap, labw = 15, 2, 62
    cols = len(arms)
    width = labw + cols * (cell + gap) + 150
    height = 46 + len(qids) * (cell + gap) + 10
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">']
    for ci, a in enumerate(arms):
        x = labw + ci * (cell + gap)
        parts.append(f'<text x="{x+cell/2:.0f}" y="14" class="axis" text-anchor="middle">{_esc(a)}</text>')
        parts.append(f'<text x="{x+cell/2:.0f}" y="28" class="axis2" text-anchor="middle">{_esc(arm_label(a, records[a])[:6])}</text>')
    score_map = {(r["arm"], r["qid"], r.get("run", 1)): r for a in arms for r in records[a]}
    fails = []
    for qi, q in enumerate(qids):
        y = 36 + qi * (cell + gap)
        parts.append(f'<text x="{labw-6}" y="{y+cell-2}" class="lbl" text-anchor="end">{_esc(q)}</text>')
        for ci, a in enumerate(arms):
            x = labw + ci * (cell + gap)
            r = next((v for (aa, qq, _rn), v in score_map.items() if aa == a and qq == q), None)
            if r is None:
                cls = "cell-na"
                tip = "未评测"
            else:
                s = r.get("score", 0)
                cls = "cell-1" if s >= 1 else ("cell-05" if s >= 0.5 else "cell-0")
                tip = "score=%s %s" % (s, ("；" + "；".join(r.get("reasons") or [])) if r.get("reasons") else "")
                if s < 1 and a == focus:
                    fails.append(r)
            parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" class="{cls}"><title>{_esc(tip)}</title></rect>')
    lx = labw + cols * (cell + gap) + 12
    for i, (cls, label) in enumerate([("cell-1", "满分 1.0"), ("cell-05", "半分 0.5"), ("cell-0", "0 分"), ("cell-na", "未跑")]):
        y = 40 + i * 18
        parts.append(f'<rect x="{lx}" y="{y}" width="10" height="10" rx="2" class="{cls}"/>')
        parts.append(f'<text x="{lx+16}" y="{y+9}" class="axis">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def metric_table(arms, agg_by_arm):
    """指标明细表：每行一个指标，列出各臂数值，并且**永远带分子/分母**。"""
    keys = list(agg_by_arm[arms[0]]["metrics"].keys())
    rows = []
    for k in keys:
        cells = []
        desc = ""
        numden = ""
        for a in arms:
            m = agg_by_arm[a]["metrics"].get(k, {})
            v = m.get("value")
            cells.append("—" if v is None else ("%.3f" % v if isinstance(v, float) else str(v)))
            desc = m.get("desc") or desc
        m0 = agg_by_arm[arms[0]]["metrics"].get(k, {})
        if m0.get("num") is not None and m0.get("den"):
            numden = "%s/%s" % (m0["num"], m0["den"])
        elif m0.get("den"):
            numden = "n=%s" % m0["den"]
        rows.append("<tr><td class='mk'>%s</td><td class='desc'>%s</td>%s<td class='nd'>%s</td></tr>"
                    % (_esc(k), _esc(desc), "".join("<td class='num'>%s</td>" % c for c in cells), _esc(numden)))
    return "".join(rows)


def build_html(arms, records, agg_by_arm, meta, focus=None) -> str:
    focus = focus or arms[-1]
    head = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>教育智能体 · 自动化评测报告</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e6e9ef;--dim:#9aa3b2;
--ok:#34d399;--mid:#fbbf24;--bad:#f87171;--na:#2b3140;--acc:#60a5fa}
*{box-sizing:border-box}
body{margin:0;padding:28px 32px 60px;background:var(--bg);color:var(--fg);
font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:24px;font-weight:600}.kpi .l{color:var(--dim);font-size:12px;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.nd{color:var(--dim);white-space:nowrap}
td.mk{font-family:ui-monospace,Consolas,monospace;color:var(--acc);white-space:nowrap}
td.desc{color:var(--dim);font-size:12px}
text.axis{fill:#8b93a3;font-size:10px}
text.axis2{fill:#5f6878;font-size:9px}
text.lbl{fill:#c8cedb;font-size:11px}
text.val{fill:#9aa3b2;font-size:10px}
line.grid{stroke:#222833;stroke-width:1}
line.whisker{stroke:#93a0b5;stroke-width:1.2;opacity:.85}
polygon.ring{fill:none;stroke:#242a35;stroke-width:1}
rect.cell-1{fill:#2f9e6a}rect.cell-05{fill:#b8860b}rect.cell-0{fill:#b3453f}rect.cell-na{fill:#2b3140}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--dim);font-size:12px;margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:12px;border:1px solid var(--line);color:var(--dim)}
.warn{border-left:3px solid var(--mid);padding-left:12px;color:#d7c9a8}
.fail{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#c8cedb}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;background:#232935;color:#aab3c2}
</style></head><body>"""
    parts = [head]
    parts.append("<h1>教育智能体 · 自动化评测报告</h1>")
    parts.append('<div class="sub">生成时间 %s　|　模型：<b>%s</b>　|　题库：%d 条输入　|　臂：%s　|　记录：%d 条</div>'
                 % (_esc(meta["time"]), _esc(meta["model"]), meta["n_questions"],
                    "、".join(arms), meta["n_records"]))
    if meta.get("mock"):
        parts.append('<div class="card warn"><b>注意：本次跑的是 mock 模型</b> —— '
                     '它只用于验证评测链路本身是否跑通，<b>不代表系统的真实效果</b>。'
                     '要得到可写进论文的数字，请用真模型重跑（见 eval/README.md）。</div>')

    # 概览卡
    parts.append('<h2>一、总体</h2><div class="grid3">')
    for a in arms:
        m = agg_by_arm[a]["metrics"]
        score = m["E0_mean_score"]["value"]
        n = len(records[a])
        passes = sum(1 for r in records[a] if r.get("pass"))
        lo, hi = M.wilson_ci(passes, n)
        parts.append(
            '<div class="kpi"><div class="v">%.3f</div><div class="l">%s · 均分（1/0.5/0 三档）</div>'
            '<div class="l">通过率 %.1f%%　95%%CI [%.1f%%, %.1f%%]</div>'
            '<div class="l">n=%d　平均延迟 %.1fs　平均 LLM 调用 %s 次</div></div>'
            % (score or 0, _esc(arm_label(a, records[a])), passes / n * 100, lo * 100, hi * 100, n,
               (m["E8_cost"].get("elapsed_ms") or 0) / 1000.0, m["E8_cost"].get("llm_calls")))
    parts.append("</div>")

    # 分组通过率
    parts.append('<h2>二、分组通过率（带 Wilson 95% 置信区间）</h2>')
    parts.append('<div class="card">%s</div>' % svg_grouped_passrate(arms, agg_by_arm))
    parts.append('<div class="legend">' + "".join(
        '<span><i style="background:%s"></i>%s</span>' % (PALETTE[i % len(PALETTE)], _esc(a))
        for i, a in enumerate(arms)) + "</div>")

    # 雷达 + 散点
    parts.append('<h2>三、能力画像与成本</h2><div class="grid2">')
    parts.append('<div class="card"><b>能力雷达</b>（7 维归一化，越外越好）%s</div>' % svg_radar(arms, agg_by_arm))
    parts.append('<div class="card"><b>成本–质量</b>（右上最好；气泡越大工具调用越多）%s</div>'
                 % svg_scatter(arms, agg_by_arm))
    parts.append("</div>")

    # 指标明细
    parts.append('<h2>四、指标明细（每个指标都带分子/分母）</h2>')
    parts.append('<div class="card"><table><thead><tr><th>指标</th><th>口径</th>'
                 + "".join("<th class='num'>%s</th>" % _esc(a) for a in arms)
                 + "<th>分子/分母</th></tr></thead><tbody>"
                 + metric_table(arms, agg_by_arm) + "</tbody></table></div>")

    # 配对检验
    core_arms = [a for a in arms if any(r["group"] != "F" for r in records[a])]
    if len(core_arms) >= 2:
        parts.append('<h2>五、臂间配对检验（McNemar 精确检验）</h2><div class="card"><table>'
                     '<thead><tr><th>对比</th><th class="num">配对题数</th><th class="num">都过</th>'
                     '<th class="num">都挂</th><th class="num">前对后错</th><th class="num">前错后对</th>'
                     '<th class="num">p 值</th><th>结论</th></tr></thead><tbody>')
        for i in range(len(core_arms)):
            for j in range(i + 1, len(core_arms)):
                a, b = core_arms[i], core_arms[j]
                cmp = M.compare_arms(records[a], records[b], a, b)
                verdict = ("差异显著（p<0.05）" if cmp["significant_0.05"]
                           else "差异不显著 —— 样本量下区分不出来")
                parts.append("<tr><td class='mk'>%s vs %s</td><td class='num'>%d</td><td class='num'>%d</td>"
                             "<td class='num'>%d</td><td class='num'>%d</td><td class='num'>%d</td>"
                             "<td class='num'>%.4f</td><td class='desc'>%s</td></tr>"
                             % (_esc(a), _esc(b), cmp["n_paired"], cmp["both_pass"], cmp["both_fail"],
                                cmp["only_%s" % a], cmp["only_%s" % b], cmp["p_value"], _esc(verdict)))
        parts.append("</tbody></table>")
        parts.append('<div class="sub">只比较两臂都跑过的同一批题（配对的前提）。'
                     'n 小时只能发现很大的差异 —— 论文里要写明这一点。</div></div>')

    # 热力图
    parts.append('<h2>六、逐题结果热力图</h2>')
    parts.append('<div class="card">%s</div>' % svg_heatmap(records, arms, focus))

    # 失败清单
    last = focus
    fails = [r for r in records[last] if r.get("score", 0) < 1]
    parts.append('<h2>七、%s（%s）的未满分题（%d 条）</h2>' % (_esc(last), _esc(arm_label(last, records[last])), len(fails)))
    parts.append('<div class="card"><table><thead><tr><th>题号</th><th>组</th><th class="num">分</th>'
                 '<th>问题</th><th>失败原因</th><th>回答片段</th></tr></thead><tbody>')
    for r in fails:
        parts.append("<tr><td class='mk'>%s</td><td>%s</td><td class='num'>%.1f</td><td>%s</td>"
                     "<td class='desc'>%s</td><td class='fail'>%s</td></tr>"
                     % (_esc(r["qid"]), _esc(GROUP_NAMES.get(r["group"], r["group"])), r.get("score", 0),
                        _esc(r["question"][:46]),
                        _esc("；".join(r.get("reasons") or []) or "—"),
                        _esc((r.get("answer") or "").replace("\n", " ")[:90])))
    if not fails:
        parts.append("<tr><td colspan='6' class='desc'>全部满分。</td></tr>")
    parts.append("</tbody></table></div>")

    parts.append('<h2>八、口径与局限（写论文时必须一并交代）</h2><div class="card">'
                 "<ul>"
                 "<li><b>三档计分</b>：1 = 规则全过且引到金标准出处；0.5 = 规则全过但没引到金标准出处；0 = 其他。</li>"
                 "<li><b>拒答指标必须成对看</b>：只报「拒答召回」会被「一律拒答」刷分，所以同时报「误拒率」。</li>"
                 "<li><b>可溯 ≠ 正确</b>：引用可溯率只证明出处没编造，不证明答案内容对。</li>"
                 "<li><b>要点覆盖是词面 proxy</b>：同义表述会漏，语义覆盖要靠 LLM 判官 + 人工校准（judge.py）。</li>"
                 "<li><b>样本量</b>：本报告 n 有限，Wilson 区间已经很宽；臂间差异要用配对检验，且不显著就别下结论。</li>"
                 "<li><b>E6 只对走工具接口的臂有意义</b>：a0 没有工具、a1 的检索写在流水线里不经过工具调用，"
                 "所以它们的 E6 结构性为 0 —— 那是设计如此，不是缺陷。要对比检索效果请看 E9–E11。</li>"
                 "<li><b>F 组（调研）n=3，不做显著性检验</b>；且本轮 arXiv 被限速，检索走的离线样例 —— "
                 "F 组的数字说明的是「流水线能不能跑通」，不是「在线检索质量」。</li>"
                 "<li><b>热力图灰色</b>=该臂没跑这道题（例如调研组 F 只有带 research 的臂会跑），不是 0 分。</li>"
                 "</ul></div>")
    return "".join(parts) + "</body></html>"


def build_markdown(arms, records, agg_by_arm, meta) -> str:
    L = []
    L.append("# 自动化评测结果（自动生成，勿手改）\n")
    L.append("- 生成时间：%s" % meta["time"])
    L.append("- 模型：`%s`%s" % (meta["model"], "（**mock，仅验链路**）" if meta.get("mock") else ""))
    L.append("- 题库：%d 条输入　记录：%d 条\n" % (meta["n_questions"], meta["n_records"]))
    L.append("## 总体\n")
    L.append("| 臂 | 说明 | n | 均分 | 通过率 | 95% CI | 平均延迟(s) | 平均 LLM 调用 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for a in arms:
        m = agg_by_arm[a]["metrics"]
        n = len(records[a])
        p = sum(1 for r in records[a] if r.get("pass"))
        lo, hi = M.wilson_ci(p, n)
        L.append("| %s | %s | %d | %.3f | %.1f%% | [%.1f%%, %.1f%%] | %.1f | %s |"
                 % (a, arm_label(a, records[a]), n, m["E0_mean_score"]["value"] or 0, p / n * 100,
                    lo * 100, hi * 100, (m["E8_cost"].get("elapsed_ms") or 0) / 1000.0,
                    m["E8_cost"].get("llm_calls")))
    L.append("\n## 指标明细\n")
    keys = list(agg_by_arm[arms[0]]["metrics"].keys())
    L.append("| 指标 | 口径 | " + " | ".join(arms) + " | 分子/分母 |")
    L.append("|---|---|" + "---|" * len(arms) + "---|")
    for k in keys:
        desc = agg_by_arm[arms[0]]["metrics"][k].get("desc", "")
        cells = []
        for a in arms:
            v = agg_by_arm[a]["metrics"].get(k, {}).get("value")
            cells.append("—" if v is None else ("%.3f" % v if isinstance(v, float) else str(v)))
        m0 = agg_by_arm[arms[0]]["metrics"][k]
        nd = ("%s/%s" % (m0["num"], m0["den"])) if m0.get("num") is not None and m0.get("den") else (
            "n=%s" % m0["den"] if m0.get("den") else "")
        L.append("| `%s` | %s | %s | %s |" % (k, desc, " | ".join(cells), nd))
    L.append("\n## 分组通过率\n")
    groups = sorted({g for a in arms for g in agg_by_arm[a]["by_group"]})
    L.append("| 组 | " + " | ".join(arms) + " |")
    L.append("|---|" + "---|" * len(arms))
    for g in groups:
        cells = []
        for a in arms:
            b = agg_by_arm[a]["by_group"].get(g)
            cells.append("—" if not b else "%.1f%% (n=%d)" % (b["pass_rate"] * 100, b["n"]))
        L.append("| %s | %s |" % (GROUP_NAMES.get(g, g), " | ".join(cells)))
    core = [a for a in arms if any(r["group"] != "F" for r in records[a])]
    if len(core) >= 2:
        L.append("\n## 配对检验（McNemar）\n")
        L.append("| 对比 | 配对题数 | 都过 | 都挂 | 前对后错 | 前错后对 | p | 结论 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i in range(len(core)):
            for j in range(i + 1, len(core)):
                a, b = core[i], core[j]
                c = M.compare_arms(records[a], records[b], a, b)
                L.append("| %s vs %s | %d | %d | %d | %d | %d | %.4f | %s |"
                         % (a, b, c["n_paired"], c["both_pass"], c["both_fail"],
                            c["only_%s" % a], c["only_%s" % b], c["p_value"],
                            "显著" if c["significant_0.05"] else "不显著"))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成评测可视化报告")
    ap.add_argument("--tag", default="", help="只看文件名带该后缀的结果")
    ap.add_argument("--focus", default="", help="把哪条臂当作被测系统（失败清单与热力图高亮）；默认 a2")
    ap.add_argument("--model", default="", help="手动指定报告上显示的模型名（记录里没有时用）")
    ap.add_argument("--open", action="store_true", help="生成后打开浏览器")
    args = ap.parse_args()

    records = load_arms(args.tag)
    if not records:
        print("没找到结果文件。请先跑：python eval/run_eval.py")
        return 1
    arms = [a for a in ARM_ORDER if a in records] + [a for a in records if a not in ARM_ORDER]
    agg_by_arm = {a: M.aggregate(records[a]) for a in arms}
    # "被测系统"：默认挑最完整的那条核心臂（a2），没有就退回 b1 / 最后一条
    focus = args.focus if args.focus in records else (
        "a2" if "a2" in records else ("b1" if "b1" in records else arms[-1]))
    # 模型名：优先用手动指定，其次读记录里的 model_id（更具体）/ model（适配层名）；都读不到就如实说明
    names = sorted({str(r.get("model")) for a in arms for r in records[a] if r.get("model")})
    ids = sorted({str(r.get("model_id")) for a in arms for r in records[a] if r.get("model_id")})
    model_name = args.model or ("、".join(ids or names) or "（记录里没写模型名，见 run_eval 输出）")
    # mock 判定要同时看两边：mock 跑的 model 是 "mock"，model_id 却是配置里的默认模型名
    mock = any("mock" in x.lower() for x in (names + ids))
    meta = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": model_name,
        "n_questions": len(M.load_questions()),
        "n_records": sum(len(v) for v in records.values()),
        "mock": mock,
        "focus": focus,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUT_DIR / "report.html"
    md_path = OUT_DIR / "report.md"
    html_path.write_text(build_html(arms, records, agg_by_arm, meta, focus), encoding="utf-8")
    md_path.write_text(build_markdown(arms, records, agg_by_arm, meta), encoding="utf-8")
    print("已生成：\n  %s\n  %s" % (html_path, md_path))
    for a in arms:
        m = agg_by_arm[a]["metrics"]
        print("  [%s] %-24s 均分 %.3f　通过率 %.1f%%"
              % (a, arm_label(a, records[a]), m["E0_mean_score"]["value"] or 0,
                 sum(1 for r in records[a] if r.get("pass")) / max(1, len(records[a])) * 100))
    if args.open:
        import webbrowser
        webbrowser.open(html_path.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
