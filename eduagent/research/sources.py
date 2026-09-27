# -*- coding: utf-8 -*-
"""来源分层与去重 —— 这个模块是"搜索引擎的特殊处理"的核心。

为什么不能直接相信搜索结果：
    把一篇 ACL 论文和一个内容农场的 SEO 文章并列返回给学生，是**负价值**。
    教学场景下，"这条资料可信吗"比"这条资料相关吗"更重要。

分层规则（沿用本人做研究时用的四层协议）：
    第 1 层：论文原文 / 正式技术报告 / 官方 benchmark 与数据集页面
    第 2 层：官方产品与技术博客 / 会议官网 / 机构发布页
    第 3 层：高质量综述、教程、可信媒体报道
    第 4 层：二手转述、社交媒体讨论
冲突处理：论文 vs 二手总结 → 以论文为准；官方页面 vs 媒体报道 → 以官方为准。
"""
import re
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------- 域名分层
TIER1_HOSTS = (
    "arxiv.org", "aclanthology.org", "openreview.net", "proceedings.neurips.cc",
    "proceedings.mlr.press", "dl.acm.org", "ieeexplore.ieee.org", "ojs.aaai.org",
    "papers.nips.cc", "link.springer.com",
)
TIER2_HOSTS = (
    "openai.com", "anthropic.com", "deepmind.google", "ai.googleblog.com",
    "research.google", "huggingface.co", "pytorch.org", "tensorflow.org",
    "docs.python.org", "github.com", "modelcontextprotocol.io", "nousresearch.com",
    "microsoft.com", "meta.com", "nvidia.com", "kdd.org", "neurips.cc",
)
TIER3_HOSTS = (
    "github.io",            # 个人博客常见（需再看内容质量）
    "infoq.cn", "infoq.com", "thenewstack.io", "towardsdatascience.com",
    "developer.aliyun.com", "cloud.tencent.com", "segmentfault.com",
    "medium.com", "substack.com",
)
TIER4_HOSTS = (
    "zhihu.com", "zhuanlan.zhihu.com", "csdn.net", "juejin.cn", "jianshu.com",
    "blog.csdn.net", "reddit.com", "twitter.com", "x.com", "weibo.com",
    "stackoverflow.com", "quora.com",
)

QUALITY_POSITIVE = ("survey", "review", "tutorial", "benchmark", "dataset", "documentation")
QUALITY_NEGATIVE = ("10分钟", "一文读懂", "保姆级", "收藏", "必看", "top 10", "listicle")


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower().lstrip("www.")


def classify(url: str = "", venue: str = "", title: str = "",
             source_type: str = "") -> Tuple[int, str]:
    """返回 (层级 1..4, 判级理由)。

    规则优先、可解释 —— 不交给模型拍脑袋，因为"为什么给这条打 1 级"在答辩上必须答得出来。
    """
    host = host_of(url)
    st = (source_type or "").lower()
    if st in ("paper", "preprint"):
        return 1, "论文/预印本原文（source_type=%s）" % st
    if st in ("official_doc", "official_blog"):
        return 2, "官方文档/官方博客"
    for h in TIER1_HOSTS:
        if host.endswith(h):
            return 1, "出版方或论文库域名（%s）" % h
    for h in TIER2_HOSTS:
        if host.endswith(h):
            # 个人 repo 与官方组织页要分开看，这里按域名给 2 级，由人工复核
            return 2, "官方/机构域名（%s）" % h
    low_title = (title or "").lower()
    if any(k in low_title for k in QUALITY_POSITIVE):
        return 3, "标题含综述/教程/benchmark 等标识"
    for h in TIER3_HOSTS:
        if host.endswith(h):
            return 3, "技术博客/教程平台（%s）" % h
    for h in TIER4_HOSTS:
        if host.endswith(h):
            return 4, "社区/问答/自媒体（%s）" % h
    if any(k in (title or "") for k in QUALITY_NEGATIVE):
        return 4, "标题为典型标题党写法"
    return 4, "未知来源，保守按最低层处理"


TIER_LABEL = {1: "★★★ 论文/官方基准", 2: "★★ 官方博客与文档",
              3: "★ 综述/教程/技术博客", 4: "⚠ 社区讨论，仅作线索"}


def _norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())[:60]


def dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """实体归并：同一篇论文常以 arXiv / 会议 / 博客 / 知乎帖子 四种面孔出现。

    归并键优先级：arxiv_id / doi > 归一化标题前缀。
    归并时取**层级最高**的那条作为代表，其余记进 aliases（保留证据链）。
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for it in items:
        aid = (it.get("arxiv_id") or "").strip()
        doi = (it.get("doi") or "").strip().lower()
        key = ("arxiv:" + aid) if aid else (("doi:" + doi) if doi else "t:" + _norm_title(it.get("title", "")))
        if not key or key == "t:":
            key = "u:" + (it.get("url") or "")
        cur = groups.get(key)
        if cur is None:
            it = dict(it)
            it["aliases"] = []
            groups[key] = it
        else:
            if it.get("tier", 9) < cur.get("tier", 9):
                keep, drop = dict(it), cur          # 层级更高者当代表
            else:
                keep, drop = cur, it
            keep = dict(keep)
            keep["aliases"] = list(cur.get("aliases", [])) + [
                {"title": drop.get("title"), "url": drop.get("url"), "tier": drop.get("tier")}]
            groups[key] = keep
    out = list(groups.values())
    out.sort(key=lambda x: (x.get("tier", 9), x.get("year", 0) or 0))
    return out


def summarize_tiers(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """统计分层分布 —— 这是评测指标"权威来源占比"的直接来源。"""
    n = len(items) or 1
    by = {1: 0, 2: 0, 3: 0, 4: 0}
    for it in items:
        by[int(it.get("tier", 4))] = by.get(int(it.get("tier", 4)), 0) + 1
    return {
        "total": len(items),
        "tier_counts": by,
        "authoritative_ratio": round((by[1] + by[2]) / n, 3),   # 1、2 层算"权威"
        "community_ratio": round(by[4] / n, 3),
    }
