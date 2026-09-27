# -*- coding: utf-8 -*-
"""内部统一消息格式与工具描述。

内部只用一种格式（OpenAI 风格），不同模型/供应商的差异放在 llm/ 里做转换。
这样主循环对"用哪个模型"完全无感 —— 换模型不动循环，就是分层的好处。
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"      # stop | tool_calls | length
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    """一个工具的完整声明：给模型看的 schema + 给运行时用的实现与门控。"""
    name: str
    description: str
    parameters: Dict[str, Any]                  # JSON Schema
    handler: Callable[..., Any]
    toolset: str = "core"                       # 分组，用于 --toolsets 过滤
    interactive: bool = False                   # True = 必须串行执行（如反问用户）
    check_fn: Optional[Callable[[], bool]] = None   # 可用性门控

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool_result(call_id: str, content: str) -> Dict[str, Any]:
    """工具结果回填成一条 tool 消息（与 Hermes 的做法一致）。"""
    return {"role": "tool", "tool_call_id": call_id, "content": content}
