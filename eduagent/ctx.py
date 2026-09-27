# -*- coding: utf-8 -*-
"""运行时上下文：让工具能拿到"当前是谁在问、哪个会话"。

为什么要这么绕一层：工具签名是给模型看的（schema 里不能出现 session_id 这种内部字段），
所以会话信息不能从参数传，只能从运行时上下文取。
主循环在执行工具前 set 一次，工具内部用 current_session() 读。
"""
import contextvars

_session: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")
_turn: contextvars.ContextVar[int] = contextvars.ContextVar("turn", default=0)


def set_session(session_id: str, turn: int = 0) -> None:
    _session.set(session_id)
    _turn.set(turn)


def current_session() -> str:
    return _session.get()


def current_turn() -> int:
    return _turn.get()
