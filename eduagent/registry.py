# -*- coding: utf-8 -*-
"""工具注册表 —— "注册优先于枚举"。

设计要点（照 Hermes Agent 的分层，但代码是自己写的）：
1. 工具在 **import 时** 自己调用 register() 注册，没有中心清单文件；
   加一个工具 = 加一个 .py 文件，主循环一个字都不用改。
2. toolset 只做 **过滤**，不是"加载不同代码"。
3. check_fn 做可用性门控（例如 RAGLearn 没配好时，检索工具就不该出现在工具清单里），
   带 TTL 缓存，避免每次向模型要工具清单都去探一遍环境。
"""
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from .protocols import ToolSpec

_REGISTRY: Dict[str, ToolSpec] = {}
_CHECK_CACHE: Dict[str, tuple] = {}
CHECK_TTL_SECONDS = 30.0


def register(name: str, description: str, parameters: Dict[str, Any],
             toolset: str = "core", interactive: bool = False,
             check_fn: Optional[Callable[[], bool]] = None):
    """装饰器：把 handler 注册成模型可调用的工具。"""
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            # 幂等覆盖：允许插件/热重载重复注册，不抛异常（照 Hermes 的做法）
            print("[registry] WARN 覆盖已注册工具: %s" % name)
        _REGISTRY[name] = ToolSpec(
            name=name, description=description, parameters=parameters,
            handler=fn, toolset=toolset, interactive=interactive, check_fn=check_fn,
        )
        return fn
    return deco


def _is_available(spec: ToolSpec) -> bool:
    """带 TTL 缓存的可用性判断。"""
    if spec.check_fn is None:
        return True
    now = time.time()
    hit = _CHECK_CACHE.get(spec.name)
    if hit and now - hit[0] < CHECK_TTL_SECONDS:
        return hit[1]
    try:
        ok = bool(spec.check_fn())
    except Exception as exc:            # 门控本身出错不应拖垮整个工具清单
        print("[registry] check_fn 异常 %s: %r -> 视为不可用" % (spec.name, exc))
        ok = False
    _CHECK_CACHE[spec.name] = (now, ok)
    return ok


def discover(package: str = "eduagent.tools") -> List[str]:
    """import 包下所有模块，触发自注册。"""
    import importlib
    import pkgutil
    mod = importlib.import_module(package)
    names = []
    for m in pkgutil.iter_modules(mod.__path__):
        if m.name.startswith("_"):
            continue
        importlib.import_module("%s.%s" % (package, m.name))
        names.append(m.name)
    return names


def get_tools(toolsets: Optional[Iterable[str]] = None,
              exclude: Iterable[str] = ()) -> List[ToolSpec]:
    """按 toolset 过滤出当前可用的工具。"""
    want = set(toolsets) if toolsets else None
    skip = set(exclude)
    out = []
    for spec in _REGISTRY.values():
        if spec.name in skip:
            continue
        if want is not None and spec.toolset not in want and spec.toolset != "core":
            continue
        if not _is_available(spec):
            continue
        out.append(spec)
    return sorted(out, key=lambda s: s.name)


def schemas(toolsets: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    return [s.schema() for s in get_tools(toolsets)]


def get(name: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(name)


def names() -> List[str]:
    return sorted(_REGISTRY)
