# -*- coding: utf-8 -*-
"""研究方向调研与学习规划工具集（toolset="research"）。

六步流水线，工具之间通过**会话级工作区**交换中间结果，只传 topic 这个 key：
    1 research_search      多源检索（当前实现 arXiv 官方 API）
    2 fetch_source         抓取并清洗正文（**没抓成功的 URL 不许进最终清单**）
    3 grade_sources        来源分层 + 实体去重 → 权威占比
    4 build_topic_map      结构化：概念 / 方法 / 时间线 / 未解问题
    5 plan_learning_route  生成带时间配比与前置依赖的学习路线
    6 save_research_note   落盘成 Markdown（可增量更新）

为什么"检索"要单独做成一个模块（答辩必答）：
    课程知识库检索（RAG）回答的是"这个知识点是什么"，
    研究方向检索回答的是"这个方向我该怎么入门" —— 后者是开放世界、来源质量参差、
    且**结果必须是结构化的路线而不是链接列表**。这两件事的工程处理完全不同。
"""
import json
import re
import time
from typing import Any, Dict, List

from .. import config
from ..registry import register
from ..research import arxiv, fetch, route as route_mod, sources as src_mod
from ..research import workspace as ws

RESEARCH_HINT = ("怎么学", "入门", "学习路线", "研究方向", "调研", "有哪些资料",
                 "该看什么", "路线", "systematic", "最新进展")


def _check_net() -> bool:
    """门控：没有任何可用的检索通道时，这批工具不该出现在模型面前。"""
    return bool(config.ENABLE_RESEARCH)


# ---------------------------------------------------------------- 1 检索
@register(
    name="research_search",
    description=("调研一个研究方向：多源检索并自动做来源分层与去重，结果存入本会话的调研工作区。"
                 "学生问『XX 方向怎么入门 / 有哪些资料 / 最新进展』时先调它。"
                 "参数 topic 用中文或英文都行，越具体越好。"),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "研究方向的名称，如 教育智能体 / tool learning"},
            "query": {"type": "string", "description": "可选的英文检索词（arXiv 以英文为主，给一个更准）"},
            "since": {"type": "string", "description": "时间窗起点，如 2024-01-01；不传则不限"},
            "limit": {"type": "integer", "description": "条数，默认 8", "default": 8},
        },
        "required": ["topic"],
    },
    toolset="research",
    check_fn=_check_net,
)
def research_search(topic: str, query: str = None, since: str = None, limit: int = 8) -> str:
    q = query or _to_query(topic)
    items: List[Dict[str, Any]] = []
    errors = []
    for attempt_q in [q, topic]:                      # 英文命中不了就退回中文原词
        try:
            items = arxiv.search(attempt_q, limit=limit, since=since, sort="relevance")
            if items:
                break
        except Exception as exc:
            errors.append(repr(exc)[:120])
    if not items:
        return "检索失败或没有结果。%s（可以换英文关键词重试，或换个说法）" % (
            "错误：" + "；".join(errors) if errors else "")
    for it in items:                                   # 立即打分层，便于后续按层级筛
        it["tier"], it["tier_reason"] = src_mod.classify(
            url=it.get("url", ""), venue=it.get("venue", ""),
            title=it.get("title", ""), source_type=it.get("source_type", ""))
    before = len(items)
    items = src_mod.dedupe(items)
    info = ws.add_candidates(topic, items)
    stat = src_mod.summarize_tiers(ws.load(topic)["candidates"])
    head = "已检索 topic=%s，关键词=%s：新增 %d 条（去重前 %d → 去重后 %d），工作区共 %d 条。" % (
        topic, attempt_q, info["added"], before, len(items), info["total"])
    lines = [head, "分层分布：" + "、".join(
        "%d层 %d 条" % (k, v) for k, v in stat["tier_counts"].items() if v),
        "权威来源占比：%.0f%%" % (stat["authoritative_ratio"] * 100), ""]
    for it in items[:6]:
        lines.append("- [%d层] %s (%s) %s" % (it["tier"], it.get("title", "")[:70],
                                             it.get("published") or it.get("year") or "?",
                                             it.get("url", "")))
    lines.append("\n（下一步：fetch_source 抓取正文，验证链接真实可达）")
    return "\n".join(lines)


def _to_query(topic: str) -> str:
    """中文方向名 → 英文检索词的小词典 + 兜底原词。真实系统应接翻译或双语词表。"""
    table = {
        "教育智能体": "educational agent", "智能体": "LLM agent", "大模型": "large language model",
        "检索增强": "retrieval augmented generation", "工具调用": "tool calling",
        "多轮对话": "multi-turn dialogue", "知识库": "knowledge base",
        "学习路径": "learning path recommendation", "自动评测": "automatic evaluation",
    }
    for k, v in table.items():
        if k in topic:
            return v
    return topic


# ---------------------------------------------------------------- 2 抓取
@register(
    name="fetch_source",
    description=("抓取资料链接的正文并清洗。**引用任何网络来源之前都必须调用它验证链接可达**，"
                 "否则不允许写进最终资料清单。默认抓工作区里还没验证过的前 N 条，也可指定 url。"),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "调研主题（工作区 key）"},
            "url": {"type": "string", "description": "指定要抓的链接；不传则自动抓未验证的"},
            "max_items": {"type": "integer", "description": "自动抓取的条数，默认 3", "default": 3},
        },
        "required": ["topic"],
    },
    toolset="research",
    check_fn=_check_net,
)
def fetch_source(topic: str, url: str = None, max_items: int = 3) -> str:
    d = ws.load(topic)
    todo = [c for c in d["candidates"] if not c.get("fetched") and
            c.get("source_type") not in ("paper", "preprint")]
    if url:
        todo = [{"url": url, "title": url}] + [c for c in todo if c["url"] != url]
    todo = todo[: max(1, int(max_items))]
    if not todo:
        n_paper = len([c for c in d["candidates"] if c.get("source_type") in ("paper", "preprint")])
        return "没有待验证的网页链接（论文条目 %d 条已由官方 API 直接提供元数据）。" % n_paper
    lines = []
    ok_n = 0
    for item in todo:
        r = fetch.fetch_text(item["url"])
        ws.mark_fetched(topic, item["url"], r["ok"], r["chars"], r.get("text", ""))
        if r["ok"]:
            ok_n += 1
        lines.append("- %s %s（%d 字符）%s" % (
            "✅" if r["ok"] else "❌", r.get("title") or item["url"][:60],
            r["chars"], ("" if r["ok"] else " 原因：" + r.get("error", "失败"))))
    return "抓取验证 %d 条，成功 %d 条：\n%s\n（未验证的链接不会进入最终清单）" % (
        len(todo), ok_n, "\n".join(lines))


# ---------------------------------------------------------------- 3 分级与去重
@register(
    name="grade_sources",
    description=("对工作区里的资料做来源分层统计与实体去重，给出'权威来源占比'。"
                 "生成学习路线之前调用它，用来判断资料是否够权威。"),
    parameters={
        "type": "object",
        "properties": {"topic": {"type": "string", "description": "调研主题"}},
        "required": ["topic"],
    },
    toolset="research",
    check_fn=_check_net,
)
def grade_sources(topic: str) -> str:
    d = ws.load(topic)
    items = ws.verified(d)
    if not items:
        return "工作区里没有可用资料，请先 research_search。"
    stat = src_mod.summarize_tiers(items)
    lines = ["可用资料 %d 条；权威来源占比 %.0f%%（第 1、2 层）；社区来源占比 %.0f%%。" % (
        stat["total"], stat["authoritative_ratio"] * 100, stat["community_ratio"] * 100)]
    for lvl in (1, 2, 3, 4):
        sub = [x for x in items if int(x.get("tier", 4)) == lvl]
        if not sub:
            continue
        lines.append("\n%s（%d 条）" % (src_mod.TIER_LABEL[lvl], len(sub)))
        for x in sub[:5]:
            lines.append("  - %s  %s" % (x.get("title", "")[:66], x.get("url", "")))
    if stat["authoritative_ratio"] < 0.5:
        lines.append("\n⚠ 权威来源不足一半，建议补检索（换英文关键词 / 加时间窗）后再生成路线。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 4 结构化
STOP = set("a an the of for and to in on with via using towards toward based toward under "
           "learning model models method methods approach approaches study analysis".split())


def _keywords(texts: List[str], top: int = 12) -> List[str]:
    freq: Dict[str, int] = {}
    for t in texts:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", (t or "").lower()):
            if w in STOP or len(w) < 3:
                continue
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:top]]


@register(
    name="build_topic_map",
    description=("把工作区里的资料整理成结构化的主题地图：核心概念、方法类别、时间线、"
                 "未解问题。生成学习路线之前调用它。"),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "调研主题"},
        },
        "required": ["topic"],
    },
    toolset="research",
    check_fn=_check_net,
)
def build_topic_map(topic: str) -> str:
    d = ws.load(topic)
    items = ws.verified(d)
    if not items:
        return "工作区里没有可用资料，请先 research_search。"
    texts = [(x.get("title") or "") + " " + (x.get("summary") or x.get("excerpt") or "")
             for x in items]
    concepts = _keywords(texts, 12)
    years: Dict[str, int] = {}
    for x in items:
        y = str(x.get("year") or (x.get("published") or "")[:4])
        if y.isdigit():
            years[y] = years.get(y, 0) + 1
    timeline = ["%s 年：%d 篇" % (y, n) for y, n in sorted(years.items())]
    # 未解问题：从摘要/正文里找"未来工作/开放问题"类线索（规则版）
    open_q = []
    for x in items:
        blob = (x.get("summary") or x.get("excerpt") or "")
        for m in re.finditer(r"(future work[^.]*\.|open (?:problem|question|challenge)[^.]*\.|"
                             r"remains? (?:unclear|open|challenging)[^.]*\.)", blob, re.I):
            s = " ".join(m.group(0).split())
            if s not in open_q and len(s) > 20:
                open_q.append("（%s）%s" % (x.get("title", "")[:24], s[:150]))
        if len(open_q) >= 6:
            break
    methods = ["第 1 层来源 %d 条（论文原文）" % len([x for x in items if x.get("tier") == 1]),
               "第 2 层来源 %d 条（官方文档/博客）" % len([x for x in items if x.get("tier") == 2])]
    d["map"] = {"concepts": concepts, "timeline": timeline,
                "open_questions": open_q, "methods": methods, "n_items": len(items)}
    ws.save(topic, d)
    out = ["主题地图：%s（基于 %d 条资料）" % (topic, len(items)),
           "\n【高频概念】" + "、".join(concepts),
           "\n【方法/来源构成】" + "；".join(methods),
           "\n【时间线】" + "；".join(timeline)]
    if open_q:
        out.append("\n【未解问题（可做选题的线索）】")
        out += ["  - " + s for s in open_q[:4]]
    out.append("\n（下一步：plan_learning_route 生成学习路线）")
    return "\n".join(out)


# ---------------------------------------------------------------- 5 路线
@register(
    name="plan_learning_route",
    description=("按主题地图与可用时间生成学习路线：分阶段、带时间配比、带每阶段产出物。"
                 "学生问『这个方向怎么学 / 多久能入门』时用它。"),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "调研主题"},
            "hours": {"type": "integer", "description": "可用总小时数，默认 20", "default": 20},
            "level": {"type": "string", "description": "当前水平：初学 / 有基础 / 进阶", "default": "初学"},
            "prereq": {"type": "string", "description": "已知的先修项，分号分隔，如 '会 Python；懂基本 HTTP'"},
        },
        "required": ["topic"],
    },
    toolset="research",
    check_fn=_check_net,
)
def plan_learning_route(topic: str, hours: int = 20, level: str = "初学", prereq: str = "") -> str:
    d = ws.load(topic)
    items = ws.verified(d)
    if not items:
        return "工作区里没有可用资料，请先 research_search。"
    r = route_mod.plan_route(topic, items, hours=float(hours), level=level, prereq=prereq or "")
    d["route"] = r
    ws.save(topic, d)
    return route_mod.render_route(r)


# ---------------------------------------------------------------- 6 落盘
@register(
    name="save_research_note",
    description=("把本次调研的完整档案（资料清单 + 主题地图 + 学习路线）落盘成 Markdown 文件，"
                 "便于后续增量更新。调研结束时调用。"),
    parameters={
        "type": "object",
        "properties": {"topic": {"type": "string", "description": "调研主题"}},
        "required": ["topic"],
    },
    toolset="research",
    check_fn=_check_net,
)
def save_research_note(topic: str) -> str:
    d = ws.load(topic)
    items = ws.verified(d)
    if not items:
        return "工作区里没有可用资料，无从落盘。"
    stat = src_mod.summarize_tiers(items)
    out = ["# 研究方向调研：%s" % topic, "",
           "> 生成时间：%s　｜　资料 %d 条　｜　权威来源占比 %.0f%%" % (
               time.strftime("%Y-%m-%d %H:%M"), stat["total"],
               stat["authoritative_ratio"] * 100), "",
           "## 一、主题地图", ""]
    m = d.get("map") or {}
    out.append("**核心概念**：" + "、".join(m.get("concepts", [])))
    out.append("")
    out.append("**时间线**：" + "；".join(m.get("timeline", [])))
    if m.get("open_questions"):
        out.append("")
        out.append("**未解问题**")
        out += ["- " + s for s in m["open_questions"]]
    out += ["", "## 二、学习路线", ""]
    if d.get("route"):
        out.append(route_mod.render_route(d["route"]))
    else:
        out.append("（尚未生成路线）")
    out += ["", "## 三、资料清单（按来源层级排序）", ""]
    for lvl in (1, 2, 3, 4):
        sub = [x for x in items if int(x.get("tier", 4)) == lvl]
        if not sub:
            continue
        out.append("### %s" % src_mod.TIER_LABEL[lvl])
        for x in sub:
            year = x.get("published") or x.get("year") or ""
            out.append("- **%s**　%s　%s" % (x.get("title", ""), year, x.get("url", "")))
            if x.get("tier_reason"):
                out.append("  - 判级理由：%s" % x["tier_reason"])
        out.append("")
    out += ["## 四、说明", "",
            "- 本文件由教育智能体调研模块自动生成，资料链接均经过抓取验证。",
            "- 第 4 层来源仅作线索，不作为结论依据。"]
    dest = config.RUN_DIR / "research" / (_safe(topic) + ".md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out), encoding="utf-8")
    d.setdefault("notes", []).append(str(dest))
    ws.save(topic, d)
    return "调研档案已写入：%s（%d 字符，%d 条资料）" % (dest, len("\n".join(out)), len(items))


def _safe(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", s)[:60] or "topic"
