# -*- coding: utf-8 -*-
"""模型适配层：内部只认一种消息格式，差异都关在这一层里。

换模型 / 换供应商 = 加一个文件 + 改 config，主循环一个字不动。
"""
from .base import LLM

__all__ = ["LLM", "get_llm"]


def get_llm(name: str = "auto", **kw) -> LLM:
    """auto：有 API key 就用真模型，没有就用 mock（保证任何人克隆下来都能跑）。"""
    from .. import config
    if name == "mock" or (name == "auto" and not config.LLM_API_KEY):
        from .mock import MockLLM
        return MockLLM(**kw)
    from .deepseek import OpenAICompatLLM
    return OpenAICompatLLM(**kw)
