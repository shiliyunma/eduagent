# 教育智能体运行时 · 起步脚手架

> 这是毕设的**第 0 天代码**：一条能跑通的端到端链路 + 一套可扩展的分层。
> 作者：张思浩（南京邮电大学 软件工程 2027 届）　建立：2026-09-27　版本 0.1.0

---

## 一、3 条命令跑起来

```bash
cd "D:/AI+教育毕设毕设/毕设规划/脚手架"

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

用真模型：复制 `.env.example` 成 `.env`，填 `LLM_API_KEY`（你的 DeepSeek key），
再 `set -a && source .env && set +a`（或直接在环境变量里设）。

### 两条检索通道怎么切换（现场演示用）

```bash
python -m eduagent.cli --toolsets core            "快速排序的时间复杂度"   # 只有课程问答
python -m eduagent.cli --toolsets core,research   "帮我调研一下教育智能体方向"  # 加上调研通道
python -m eduagent.cli --toolsets core            "帮我调研一下教育智能体方向"  # 只有 core 时它会说找不到
```

> 这就是"**加一条通道 = 加一个工具集目录**"的现场证据：`research` 那 6 个工具
> 是后加的，主循环、注册表、上下文模块**一个字都没改**。

---

## 二、这套代码里"已经实现"的机制（论文第 3 章就是这个清单）

| # | 机制 | 在哪 | 解决什么问题 | 对照 Hermes Agent |
|---|---|---|---|---|
| 1 | 同步主循环 + `finish_reason` 三分支 | `eduagent/loop.py` | 模型要调工具还是要收尾，由 `finish_reason` 决定 | `agent/conversation_loop.py` 的 `run_conversation` |
| 2 | 工具**自注册**（import 即注册） | `eduagent/registry.py` | 加工具不用改中心清单，不动主循环 | `tools/registry.py:register()` |
| 3 | toolset 过滤 + `check_fn` 门控（带 TTL 缓存） | `registry.py` | 工具不可用时不该出现在模型面前；门控不能每轮都探一遍 | `_CHECK_FN_TTL_SECONDS = 30` |
| 4 | 迭代预算，超限优雅退出 | `loop.py` + `config.MAX_ITERATIONS` | 防死循环；也是论文要做"步数上限"对照实验的旋钮 | `agent/iteration_budget.py` |
| 5 | 多工具调用并发 + **按原顺序回填** | `loop.py:_run_tools` | 一轮多个工具省时间；顺序错了模型会读乱 | `agent/tool_executor.py` |
| 6 | 三层提示词（stable / context / volatile） | `eduagent/context.py` | 易变内容放最后，前缀缓存才不破 | `agent/system_prompt.py:build_system_prompt_parts` |
| 7 | 上下文预算 + 压缩（保头保尾、**工具对不拆**） | `context.py` | 长对话会爆；拆开工具调用和结果会让模型行为错乱 | `agent/context_compressor.py` |
| 8 | 记忆**快照**（会话开始读一次，会话内不变） | `eduagent/memory.py` | 记忆要持久，但不能改了当前会话的提示词 | Hermes 的 frozen snapshot 模式 |
| 9 | SQLite + WAL + FTS5 持久化 | `eduagent/store.py` | 会话跨进程存活 + 历史可检索 | `hermes_state.py`（简化版） |
| 10 | **轨迹落表**（每次模型/工具调用都记） | `store.trace()` | 论文的实验数据从这来，不靠事后回忆 | `agent/trajectory.py` |
| 11 | 工具执行上下文（`contextvars` 传会话） | `eduagent/ctx.py` | 工具签名要干净，会话信息不能塞进 schema | `task_id` 的隔离思路 |
| 12 | 模型适配层（mock / OpenAI 兼容） | `eduagent/llm/` | 换模型不动主循环；没 key 也能跑测试 | `agent/anthropic_adapter.py` 等 |
| 13 | **来源分层**（规则驱动，四层） | `research/sources.py` | 把"相关"和"可信"分开 —— 教学场景里后者更重要 | 本人的人工调研协议 |
| 14 | **抓取验证**（没抓成功的 URL 不进清单） | `research/fetch.py` | 防模型编造链接（这是开放世界检索最大的坑）| — |
| 15 | **会话级调研工作区** | `research/workspace.py` | 中间结果在工具之间流动，而不是在上下文里流动 | `execute_code` 的 RPC 思路 |
| 16 | **学习路线生成**（确定性规则 + 时间配比） | `research/route.py` | 答辩要能解释"路线怎么生成的"，不能是模型拍脑袋 | — |
| 17 | **限速 + 缓存 + 离线兜底** | `research/arxiv.py` | 被限速或断网时链路不崩（答辩现场保险）| — |

**这张表就是"我读懂了 Hermes 的分层，并且自己实现了一遍"的证据。**
面试被追问时，指着一张表说"这一层它怎么做的、我怎么做的、差在哪"，比背概念有效得多。

---

## 三、目录结构

```
脚手架/
├── check.py                 自检（6 项，全绿再动手）
├── run_demo.py              端到端演示（免 key）
├── requirements.txt         最小依赖（只有 requests）
├── .env.example             配置模板
├── data/
│   ├── sample_kb.jsonl      课程知识样例（10 条，接 RAGLearn 前的替身）
│   ├── sample_exercise.jsonl 习题样例（5 条）
│   └── syllabus.json        课程大纲（学习路径规划用）
├── run/                     运行产物（自动生成，已 gitignore）
│   ├── eduagent.db          SQLite：会话 / 消息 / 学情 / 轨迹
│   ├── MEMORY.md            学生画像快照
│   └── trace.jsonl          轨迹（备查）
└── eduagent/
    ├── config.py            ★ 所有参数集中在这（论文"参数设置"直接引它）
    ├── protocols.py         内部统一消息格式
    ├── registry.py          工具注册表
    ├── ctx.py               运行时上下文
    ├── store.py             SQLite 持久化 + 轨迹
    ├── memory.py            MEMORY.md 快照
    ├── context.py           提示词组装 + 预算 + 压缩
    ├── loop.py              ★ 主循环
    ├── cli.py               命令行入口
    ├── llm/{base,mock,deepseek}.py
    └── tools/{kbsearch,exercise,profile}.py
```

---

## 四、加一个工具要做什么（30 秒演示"加文件就够"）

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

## 五、接上 RAGLearn 的真实检索（第二个周末要做的事）

```bash
USE_RAGLEARN=1 RAGLEARN_HOME="E:/RAGLearn" python -m eduagent.cli "B+树为什么适合做索引"
```

`tools/kbsearch.py` 里已留好接入点 `_search_raglearn()`：动态 `import query` →
调用 `multi_retrieve(query)` → 把 `(docs, embed_ms, retrieval_ms, display_queries)` 转成本项目的格式。
注意 RAGLearn 用的是 ChromaDB + bge 嵌入 + Cross-Encoder 重排，第一次跑会加载模型（要显存、要等）。

> ⚠️ 接的时候一定保留**降级路径**：USE_RAGLEARN=1 失败要能自动落回样例数据，
> 否则答辩现场检索一挂，整段演示就走不下去了。

---

## 六、接下来该做什么（按优先级）

1. **加 1 个你自己的工具**（比如 `make_quiz` 或 `summarize_document`），跑通 → git commit
2. **接 RAGLearn 检索**，让 `search_course_kb` 走真实向量检索
3. **补一个 Web 界面**（Streamlit，照 RAGLearn 的风格），便于录答辩演示
4. **按《03-评测与实验方案》建评测集**，把 60 道题和标注表建起来
5. **做对照实验**：纯 LLM / 纯 RAG / 本智能体 三档跑一遍，结果落表
6. **调研通道的消融开关**（`ENABLE_LAYERING` / `EDUAGENT_NO_FETCH`）与 B0 基线脚本
   —— 见 `08-研究方向调研与学习规划.md` 第六节；这是"第二个研究点"的实验基础

> 主线（1–5）优先于调研线（6）。**评测集是论文第 5 章的地基，永远优先。**

---

## 七、已知的坑（先记住，能省几小时）

| 坑 | 现象 | 处理 |
|---|---|---|
| httpx 在本机连本机端口 | `WinError 10061` | 一律用 `requests` 直连（本脚手架已这么做） |
| 中文按空格分词 | 检索一条都命不中 | 中文要切二元组（`kbsearch._query_tokens` 已处理） |
| 工具 schema 嵌套层 | 参数生成不出来（`missing required argument`） | `tools[i]["function"]["parameters"]`，别写错层 |
| SQLite 并发写 | `database is locked` | 已开 WAL；写入别放在长事务里 |
| git 提交 .env / db | 泄露 key、仓库变脏 | 用仓库里的 `.gitignore` |
| **arXiv API 返回 406** | 明明 curl 能通，urllib 报 `HTTPError 406: Not Acceptable` | **必须显式带 `Accept` 头**（curl 默认发 `Accept: */*`，urllib 默认不发）。`arxiv.py` 已修好 |
| **arXiv 被限速** | 连续请求后所有查询都 406/503 | 请求间隔 ≥3 秒 + 指数退避；**结果缓存 6 小时**；再不行落回离线样例（`data/fixtures_arxiv.jsonl`） |
| 网页抓取拿到导航不拿到正文 | 正文里全是菜单和页脚 | `fetch.py` 先按 `<article>/<main>/id="content"` 定位正文区再清洗 |
| 模型编造链接 | 资料清单里出现打不开的 URL | **硬约束**：未通过 `fetch_source` 验证的 URL 不进最终清单（评测里专门有"编造率"指标） |

---

## 八、验收标准（做到这些才算"骨架跑通"）

- [ ] `python check.py` 6 项全绿
- [ ] `python run_demo.py` 能看到：工具调用 → 结果回填 → 最终回答 → 薄弱点入库 → 新会话命中薄弱点
- [ ] `python -m eduagent.cli "你的问题"` 用真模型能回答，且回答里有【出处】
- [ ] 自己新加的工具出现在 `--tools` 列表里，且能被模型自动调用
- [ ] `git log` 至少有 5 次有意义的提交（不是一次全推）
