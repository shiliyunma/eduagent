# -*- coding: utf-8 -*-
"""OpenAI 兼容接口的直连实现（DeepSeek / Ollama / vLLM / 官方 OpenAI 都能用）。

为什么不用 openai 客户端、直接 requests.post：
RAGLearn 踩过的坑 —— httpx 在 Windows + git-bash 环境连本机端口会报 WinError 10061。
直连 requests 少一层依赖，换 base_url 就能换供应商。
"""
import json
import time
from typing import Any, Dict, List, Optional

import requests

from .. import config
from ..protocols import LLMResponse, ToolCall
from .base import LLM

RETRY_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatLLM(LLM):
    name = "openai-compat"

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None,
                 temperature: float = None, timeout: int = None,
                 max_retries: int = 2, **_ignored):
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.api_key = api_key or config.LLM_API_KEY
        self.model = model or config.LLM_MODEL
        self.temperature = config.LLM_TEMPERATURE if temperature is None else temperature
        self.timeout = timeout or config.LLM_TIMEOUT
        self.max_retries = max_retries
        if not self.api_key:
            raise RuntimeError("没有配置 API key：请在 .env 里设置 LLM_API_KEY（或 DEEPSEEK_API_KEY）")

    def chat(self, messages: List[Dict[str, Any]],
             tools: Optional[List[Dict[str, Any]]] = None) -> LLMResponse:
        url = self.base_url + "/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(url, headers={
                    "Authorization": "Bearer " + self.api_key,
                    "Content-Type": "application/json",
                }, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=self.timeout)
                if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                    wait = 2 ** attempt
                    print("[llm] %s -> 退避 %ss 重试" % (r.status_code, wait))
                    time.sleep(wait)
                    continue
                if r.status_code >= 400:
                    raise RuntimeError("HTTP %s: %s" % (r.status_code, r.text[:400]))
                data = r.json()
                return self._parse(data)
            except requests.RequestException as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("调用模型失败（重试 %d 次）：%r" % (self.max_retries, exc))
        raise RuntimeError("调用模型失败：%r" % last_err)

    @staticmethod
    def _parse(data: Dict[str, Any]) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls: List[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = {"_raw": raw}          # 参数解析失败也要让循环继续，由工具层报错
            calls.append(ToolCall(id=tc.get("id") or ("call_%d" % i),
                                  name=fn.get("name") or "", arguments=args))
        finish = choice.get("finish_reason") or ("tool_calls" if calls else "stop")
        return LLMResponse(content=msg.get("content") or "", tool_calls=calls,
                           finish_reason=finish, usage=data.get("usage") or {}, raw=data)
