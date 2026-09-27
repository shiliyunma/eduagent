# EduAgent · 教育智能体运行时

一个**自研**的教育智能体运行时：把「模型 + 工具调用循环 + 记忆 + 两条检索通道」这套骨架自己实现一遍，
而不是套一个现成框架。

- **两条检索通道**：`core` 面向已知的课程知识库（回答"这个知识点是什么"，带出处）；
  `research` 面向开放世界（回答"这个方向该怎么学"，来源分层 + 抓取验证 + 学习路线生成）。
- **一条主循环两边共用**：加一条通道 = 加一个工具集目录，主循环、注册表、上下文模块一行都不用改。
- **零依赖也能跑**：内置 mock 模型，不需要 API key、不需要联网就能跑完整链路；
  真实检索源（arXiv / 网页）被限速或断开时自动落回离线样例。

```bash
git clone git@github.com:shiliyunma/eduagent.git && cd eduagent
```

作者：张思浩（南京邮电大学 软件工程 2027 届）　版本 0.1.0　Python 3.11+

---

## 一、快速开始

```bash
# 1) 自检（不联网、不需要 key，6 项全绿才算环境没问题）
python check.py

# 2) 端到端演示（假模型，看到完整证据链：工具调用 → 回填 → 回答 → 记忆 → 跨会话）
python run_demo.py

# 3) 用命令行问一句（配了 key 就是真模型；--mock 强制假模型）
python -m eduagent.cli "快速排序的时间复杂度是多少"
python -m eduagent.cli --tools          # 看注册了哪些工具（11 个）
python -m eduagent.cli                  # 交互模式

# 4) 研究方向调研流水线（走真实 arXiv API + 真实网页抓取，不需要 key）
python run_research_demo.py "教育智能体"
python run_research_demo.py "tool learning" --offline   # 只看离线样例
```

用真模型：复制 `.env.example` 成 `.env`，填 `LLM_API_KEY`，再 `set -a && source .env && set +a`
（或直接把变量设在环境里）。

### 两条检索通道怎么切换

```bash
python -m eduagent.cli --toolsets core            "快速排序的时间复杂度"
python -m eduagent.cli --toolsets core,research   "帮我调研一下教育智能体方向"
python -m eduagent.cli --toolsets core            "帮我调研一下教育智能体方向"   # 只有 core 时它会说找不到
```

最后一条是刻意留的：工具集过滤掉了 `research`，模型看不见那 6 个工具，
会明确告诉你"当前没有调研能力"，而不是硬编一个答案。

---

## 二、已实现的机制

| # | 机制 | 在哪 | 解决什么问题 |
|---|---|---|---|
| 1 | 同步主循环 + `finish_reason` 三分支 | `eduagent/loop.py` | 模型要调工具还是要收尾，由 `finish_reason` 决定 |
| 2 | 工具**自注册**（import 即注册） | `eduagent/registry.py` | 加工具不用改中心清单，不动主循环 |
| 3 | toolset 过滤 + `check_fn` 门控（带 TTL 缓存） | `registry.py` | 工具不可用时不该出现在模型面前；门控不能每轮都探一遍 |
| 4 | 迭代预算，超限优雅退出 | `loop.py` + `config.MAX_ITERATIONS` | 防死循环；也是"步数上限"对照实验的旋钮 |
| 5 | 多工具调用并发 + **按原顺序回填** | `loop.py:_run_tools` | 一轮多个工具省时间；顺序错了模型会读乱 |
| 6 | 三层提示词（stable / context / volatile） | `eduagent/context.py` | 易变内容放最后，前缀缓存才不破 |
| 7 | 上下文预算 + 压缩（保头保尾、**工具对不拆**） | `context.py` | 长对话会爆；拆开工具调用和结果会让模型行为错乱 |
| 8 | 记忆**快照**（会话开始读一次，会话内不变） | `eduagent/memory.py` | 记忆要持久，但不能改了当前会话的提示词 |
| 9 | SQLite + WAL + FTS5 持久化 | `eduagent/store.py` | 会话跨进程存活 + 历史可检索 |
| 10 | **轨迹落表**（每次模型/工具调用都记） | `store.trace()` | 实验数据从这来，不靠事后回忆 |
| 11 | 工具执行上下文（`contextvars` 传会话） | `eduagent/ctx.py` | 工具签名要干净，会话信息不能塞进 schema |
| 12 | 模型适配层（mock / OpenAI 兼容） | `eduagent/llm/` | 换模型不动主循环；没 key 也能跑测试 |
| 13 | **来源分层**（规则驱动，四层） | `research/sources.py` | 把"相关"和"可信"分开——教学场景里后者更重要 |
| 14 | **抓取验证**（没抓成功的 URL 不进清单） | `research/fetch.py` | 防模型编造链接（开放世界检索最大的坑） |
| 15 | **会话级调研工作区** | `research/workspace.py` | 中间结果在工具之间流动，而不是在上下文里流动 |
| 16 | **学习路线生成**（确定性规则 + 时间配比） | `research/route.py` | 路线要可解释，不能是模型拍脑袋 |
| 17 | **限速 + 缓存 + 离线兜底** | `research/arxiv.py` | 检索源被限速或断网时链路不崩 |

### 两条通道的六步调研流水线

```
research_search     多源检索（arXiv API + 网页）→ 候选池
      ↓
fetch_source        逐条抓取验证（抓不到的直接剔除，不许进清单）
      ↓
grade_sources       四层来源分层 + 实体去重 + 权威占比统计
      ↓
build_topic_map     主题地图：核心概念 / 时间线 / 未解问题
      ↓
plan_learning_route 学习路线：五阶段 + 时间配比 + 每阶段产出物
      ↓
save_research_note  落盘一份 Markdown 档案（资料清单带判级理由）
```

工具之间只传 `topic` 这一个 key，中间结果落在**会话级工作区**里，
避免把几十条 JSON 塞进上下文。

---

## 三、目录结构

```
eduagent/
├── config.py             所有参数集中在这（环境变量覆盖）
├── protocols.py          内部统一消息格式
├── registry.py           工具注册表（自注册 / toolset / check_fn 门控）
├── ctx.py                运行时上下文（contextvars）
├── store.py              SQLite + WAL + FTS5 持久化 + 轨迹表
├── memory.py             MEMORY.md 快照
├── context.py            三层提示词 + token 预算 + 压缩
├── loop.py               主循环（finish_reason 三分支 / 迭代预算 / 并发工具）
├── cli.py                命令行入口
├── llm/
│   ├── base.py           统一接口
│   ├── mock.py           假模型（免 key 跑通全链路，含调研流水线的脚本化响应）
│   └── deepseek.py       OpenAI 兼容（requests 直连，不用 httpx）
├── tools/
│   ├── kbsearch.py       课程知识库检索（带出处，可切到 RAGLearn）
│   ├── exercise.py       习题检索
│   ├── profile.py        学情 / 薄弱点 / 学习路径（3 个工具）
│   └── research.py       调研六步流水线（6 个工具）
└── research/
    ├── sources.py        来源分层协议（四层）
    ├── arxiv.py          arXiv 检索（限速 / 退避 / 缓存 / 离线兜底）
    ├── fetch.py          网页抓取 + 正文清洗
    ├── workspace.py      会话级调研工作区
    └── route.py          学习路线生成（确定性规则）

data/                     样例数据（接 RAGLearn 之前的替身）
scripts/make_arxiv_fixture.py  把在线检索结果存成离线样例
check.py                  自检（6 项）
run_demo.py               课程问答端到端演示
run_research_demo.py      调研流水线演示
run/                      运行产物（自动生成，已 gitignore 掉）
```

代码规模：约 2800 行 Python，唯一第三方依赖是 `requests`。

---

## 四、加一个工具要做什么

新建 `eduagent/tools/quiz.py`：

```python
from ..registry import register

@register(
    name="make_quiz",
    description="按知识点生成练习题。学生要练习时用它。",
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "知识点"},
            "n": {"type": "integer", "description": "题数", "default": 3},
        },
        "required": ["topic"],
    },
    toolset="core",
)
def make_quiz(topic: str, n: int = 3) -> str:
    return "出题：%s × %d（这里接你的实现）" % (topic, n)
```

**不用改主循环、不用改任何清单文件。** 下次启动时 `registry.discover()` 会自动 import 它。

---

## 五、接上 RAGLearn 的真实向量检索

```bash
USE_RAGLEARN=1 RAGLEARN_HOME="E:/RAGLearn" python -m eduagent.cli "B+树为什么适合做索引"
```

`tools/kbsearch.py` 里留好了接入点 `_search_raglearn()`：动态 `import query` →
调用 `multi_retrieve(query)` → 把 `(docs, embed_ms, retrieval_ms, display_queries)` 转成本项目的格式。
RAGLearn 用的是 ChromaDB + bge 嵌入 + Cross-Encoder 重排，第一次跑要加载模型（要显存、要等）。

> ⚠️ 接入时**务必保留降级路径**：`USE_RAGLEARN=1` 失败要能自动落回样例数据，
> 否则真实检索一挂，整段演示就走不下去了。

---

## 六、已知的坑（先记住，能省几小时）

| 坑 | 现象 | 处理 |
|---|---|---|
| httpx 在本机连本机端口 | `WinError 10061` | 一律用 `requests` 直连（本项目已这么做） |
| 中文按空格分词 | 检索一条都命不中 | 中文要切二元组（`kbsearch._query_tokens` 已处理） |
| 工具 schema 嵌套层 | 参数生成不出来（`missing required argument`） | 取 `tools[i]["function"]["parameters"]`，别写错层 |
| SQLite 并发写 | `database is locked` | 已开 WAL；写入别放在长事务里 |
| git 提交 `.env` / `*.db` | 泄露 key、仓库变脏 | 用仓库里的 `.gitignore` |
| **arXiv API 返回 406** | 明明 curl 能通，urllib 报 `HTTPError 406: Not Acceptable` | **必须显式带 `Accept` 头**（curl 默认发 `Accept: */*`，urllib 默认不发）。`arxiv.py` 已修好 |
| **arXiv 被限速** | 连续请求后所有查询都 406 | 请求间隔 ≥3 秒 + 指数退避；结果**缓存 6 小时**；再不行落回离线样例（`data/fixtures_arxiv.jsonl`） |
| 网页抓取拿到导航不拿到正文 | 正文里全是菜单和页脚 | `fetch.py` 先按 `<article>/<main>/id="content"` 定位正文区再清洗 |
| 模型编造链接 | 资料清单里出现打不开的 URL | **硬约束**：未通过 `fetch_source` 验证的 URL 不进最终清单 |
| Windows 换行符 | 来回 checkout 导致整份文件 diff | 仓库根有 `.gitattributes`，统一存 LF |

---

## 七、配置与命令行

所有参数集中在 `eduagent/config.py`，其中这几项可以用环境变量覆盖（`.env.example` 是模板）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址，换供应商只改这一行 |
| `LLM_API_KEY` | 空（回退读 `DEEPSEEK_API_KEY`） | 不填就自动用 mock 模型 |
| `LLM_MODEL` | `deepseek-v4-flash` | 模型名 |
| `LLM_TEMPERATURE` | `0.2` | 采样温度 |
| `LLM_TIMEOUT` | `120` | 单次请求超时（秒） |
| `USE_RAGLEARN` | `0` | 置 `1` 改用 RAGLearn 的真实向量检索 |
| `RAGLEARN_HOME` | `E:/RAGLearn` | RAGLearn 仓库路径 |
| `ENABLE_RESEARCH` | `1` | 置 `0` 整批关掉调研工具（只剩课程问答） |
| `EDUAGENT_OFFLINE` | `0` | 置 `1` 强制走离线样例，不发 arXiv 请求 |

其余参数直接写在 `config.py` 里（改文件即可）：`MAX_ITERATIONS=12`、`TOOL_CONCURRENCY=4`、
`CONTEXT_BUDGET_TOKENS=24000`、`COMPRESS_THRESHOLD=0.50`、`PROTECT_LAST_N=8`、
`KB_TOP_K=5`、`RESEARCH_DEFAULT_LIMIT=8`、`FETCH_MIN_INTERVAL=1.0`。
运行产物统一落在 `run/`（`eduagent.db` / `MEMORY.md` / `trace.jsonl`）。

命令行：

```bash
python -m eduagent.cli "问题"                # 问一句
python -m eduagent.cli                       # 交互模式
python -m eduagent.cli --mock "问题"         # 强制假模型（免 key）
python -m eduagent.cli --tools               # 列出已注册的工具后退出
python -m eduagent.cli --toolsets core,research "问题"   # 只启用指定工具集
python -m eduagent.cli --session s_xxxxxxxx "接着上面的问"  # 续用已有会话
python -m eduagent.cli --max-iterations 6 "问题"          # 覆盖迭代预算
python -m eduagent.cli --quiet "问题"        # 少打印过程
```
