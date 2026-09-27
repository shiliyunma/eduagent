# -*- coding: utf-8 -*-
"""集中配置：路径、模型、阈值。

毕设改任何参数都改这里，不要散落在业务代码里（论文里"参数设置"一节直接引本文件）。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 脚手架根目录
DATA_DIR = ROOT / "data"
RUN_DIR = ROOT / "run"                                  # 运行产物：SQLite、记忆文件、轨迹
RUN_DIR.mkdir(exist_ok=True)

# ---- 存储 ----------------------------------------------------------------
DB_PATH = RUN_DIR / "eduagent.db"
MEMORY_PATH = RUN_DIR / "MEMORY.md"                     # 学生画像快照（进系统提示词）
TRACE_PATH = RUN_DIR / "trace.jsonl"                    # 每步轨迹，论文实验数据来源

# ---- 模型（OpenAI 兼容接口）-----------------------------------------------
# 与 RAGLearn 保持一致：直连 requests.post，不用 openai 客户端
# （RAGLearn 踩过的坑：httpx 在 Windows + git-bash 下连本机报 WinError 10061）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# ---- 主循环 --------------------------------------------------------------
MAX_ITERATIONS = 12          # 迭代预算：防死循环（论文里要做"步数上限"的对照实验）
TOOL_CONCURRENCY = 4         # 同一轮多个工具调用时的并发度
MAX_SPAWN_DEPTH = 0          # 预留：子 Agent 委派（毕设 v1 不实现）

# ---- 上下文 --------------------------------------------------------------
CONTEXT_BUDGET_TOKENS = 24000   # 估算口径见 context.estimate_tokens
COMPRESS_THRESHOLD = 0.50       # 超过预算 50% 触发压缩（与 Hermes 同口径）
PROTECT_LAST_N = 8              # 压缩时保护的最近消息条数

# ---- 检索（先本地样例，后接 RAGLearn）--------------------------------------
# 指向 RAGLearn 的仓库路径；接上以后 kbsearch 工具会自动改用它的 multi_retrieve()
RAGLEARN_HOME = os.getenv("RAGLEARN_HOME", r"E:/RAGLearn")
USE_RAGLEARN = os.getenv("USE_RAGLEARN", "0") == "1"
KB_TOP_K = 5                     # 与 RAGLearn 的"固定输出 5 条带出处上下文"对齐

# ---- 研究方向调研模块（toolset="research"）--------------------------------
# 关掉它就只剩课程问答能力（论文里做"去掉调研模块"的消融实验就靠这个开关）
ENABLE_RESEARCH = os.getenv("ENABLE_RESEARCH", "1") == "1"
RESEARCH_DEFAULT_LIMIT = 8       # 单次检索条数
FETCH_MIN_INTERVAL = 1.0         # 抓取礼节：同一进程内两次抓取的最小间隔（秒）
