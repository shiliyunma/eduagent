# -*- coding: utf-8 -*-
"""教育智能体运行时（自研）。

分层（与 Hermes Agent 同构，代码独立实现）：
    registry  工具注册表：import 即注册，toolset 过滤，check_fn 门控
    loop      主循环：finish_reason 三分支 + 迭代预算 + 并发工具执行
    context   上下文：三层提示词组装 + 预算 + 压缩
    store     SQLite 持久化：会话 / 消息 / FTS5 / 学情 / 轨迹
    memory    MEMORY.md 快照（会话开始读一次）
    llm       模型适配：mock（免 key）/ OpenAI 兼容
    tools     教育工具集：课程检索 / 习题检索 / 学情读写 / 学习路径
"""
from .loop import EduAgent

__version__ = "0.1.0"
__all__ = ["EduAgent"]
