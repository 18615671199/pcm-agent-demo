"""LLM 调用封装 —— 支持多 Provider：
  - zhongshuiruizhi  中科睿智 MaaS 私有化端点（公司自有）
  - dashscope        阿里云百炼 DashScope
  - zhipu            智谱 AI（兜底）
"""
from __future__ import annotations
import json
import os
import time
import warnings
from typing import Any, Optional

import httpx

# 内网自签证书 verify=False 时抑制 SSL 警告
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# ===== Provider 配置 =====
# 优先级：公司自有 MaaS > 阿里云百炼 > 智谱（兜底）
# 每个 provider 提供 base_url（/v1）和 endpoint（/chat/completions），代码自动拼接
PROVIDERS = {
    "zhongshuiruizhi": {
        # 中科睿智 MaaS 私有化端点 —— 公司统一接入
        "base_url": "https://maas.test.zhongshuruizhi.com:8443/v1",
        "endpoint": "/chat/completions",
        "api_key_env": "ZHONGSHUIRUIZHI_API_KEY",
        "default_model": "qwen3.7-plus",
    },
    "dashscope": {
        # 阿里云百炼 DashScope（兼容 OpenAI 格式）
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint": "/chat/completions",
        "api_key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-plus",
    },
    "zhipu": {
        # 智谱 AI —— 兜底
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "endpoint": "/chat/completions",
        "api_key_env": "ZHIPU_API_KEY",
        "default_model": "glm-4-flash",
    },
}


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        # 自动检测 provider：显式传入 > 环境变量 > 按密钥优先级自动选
        # 优先级：中科睿智（公司自有）> 阿里云百炼 > 智谱兜底
        self.provider = (
            provider
            or os.getenv("LLM_PROVIDER", "")
            or (
                "zhongshuiruizhi" if os.getenv("ZHONGSHUIRUIZHI_API_KEY")
                else "dashscope" if os.getenv("DASHSCOPE_API_KEY")
                else "zhipu"
            )
        )
        cfg = PROVIDERS[self.provider]
        self.api_key = api_key or os.getenv(cfg["api_key_env"], "")
        self.model = model or os.getenv("LLM_MODEL", "") or cfg["default_model"]
        # base_url + endpoint 自动拼接（兼容各种 Provider 格式）
        self.base_url = cfg["base_url"].rstrip("/")
        self.endpoint = cfg["endpoint"]
        self.url = self.base_url + self.endpoint
        # 内网自签证书跳过验证，公网 provider 不影响
        self._verify = (self.provider != "zhongshuiruizhi")
        self._client: Optional[httpx.Client] = None

    def _client_(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(180.0, connect=10.0),
                verify=self._verify,  # 内网 MaaS 自签证书跳过验证
            )
        return self._client

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> dict[str, Any]:
        """调用 Chat Completions，返回原始 response dict"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        resp = self._client_().post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def call_for_json(
        self, system_prompt: str, user_message: str, max_retries: int = 2
    ) -> dict[str, Any]:
        """让 LLM 直接输出 JSON，自动修复 markdown 包裹"""
        for attempt in range(max_retries + 1):
            resp = self.chat(system_prompt, user_message)
            content = resp["choices"][0]["message"]["content"]
            parsed = self._extract_json(content)
            if parsed is not None:
                return parsed
            # 重试：告诉它输出严格 JSON
            user_message = (
                f"上次输出无法解析为 JSON，请严格只输出 JSON 对象，不要 markdown 代码块。\n\n"
                f"原始请求：{user_message}"
            )
            time.sleep(0.3)
        raise RuntimeError(f"LLM 连续 {max_retries + 1} 次无法输出有效 JSON。最后输出：{content[:500]}")

    @staticmethod
    def _extract_json(text: str) -> Optional[dict[str, Any]]:
        if text is None:
            return None
        text = text.strip()
        # 去掉 ```json ... ``` 包裹
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        # 直接尝试
        try:
            return json.loads(text)
        except Exception:
            pass
        # 找第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return None
        return None
