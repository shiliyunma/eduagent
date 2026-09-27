# -*- coding: utf-8 -*-
"""模型接口定义 —— 主循环只依赖这个抽象。"""
from typing import Any, Dict, List, Optional

from ..protocols import LLMResponse


class LLM:
    name = "base"

    def chat(self, messages: List[Dict[str, Any]],
             tools: Optional[List[Dict[str, Any]]] = None) -> LLMResponse:
        raise NotImplementedError
