# -*- coding: utf-8 -*-
"""教育工具集（Agent 的"手"）。

**加一个工具 = 加一个 .py 文件**，主循环一个字都不用改。
文件里在模块顶层调用 register(...)，import 时就会自动进注册表。

每个工具要做三件事：
1. 声明给模型看的 name / description / parameters（JSON Schema）
2. 实现 handler，返回**给模型读的字符串**（不是 Python 对象）
3. 出错不要抛到循环外：返回一句能让模型自己纠正的错误说明
"""
from . import kbsearch, exercise, profile, research   # noqa: F401  —— import 即注册

__all__ = ["kbsearch", "exercise", "profile", "research"]
