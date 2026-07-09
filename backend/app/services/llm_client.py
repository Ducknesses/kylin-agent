"""LLMClient —— 统一 LLM 调用客户端

职责：
  - 封装 LLM 调用，统一返回结构
  - 支持多 provider：deepseek / local_openai_compatible
  - 复用现有 backend/app/llm/deepseek.py，不重复写 DeepSeek HTTP 调用
  - 统一处理：未启用、缺 Key、超时、网络错误、JSON 解析错误、空内容
  - 不抛出导致 WebSocket 崩溃的异常
  - 敏感信息脱敏：error 不包含 API Key / Authorization / Bearer
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


# ── 统一返回结构 ──────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """LLM 调用统一返回结构"""
    ok: bool
    content: str = ""
    error: str | None = None
    provider: str = ""
    model: str = ""
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "content": self.content,
            "error": self.error,
            "provider": self.provider,
            "model": self.model,
            "fallback_used": self.fallback_used,
        }


def _fail_response(error: str, provider: str = "", model: str = "") -> LLMResponse:
    """构造失败响应 —— error 已脱敏"""
    return LLMResponse(
        ok=False,
        content="",
        error=_sanitize_error(error),
        provider=provider,
        model=model,
        fallback_used=True,
    )


# ── 敏感信息脱敏 ──────────────────────────────────────────────────────

def _sanitize_error(text: str) -> str:
    """对错误信息做敏感信息过滤

    规则：所有敏感值统一替换为 [REDACTED]
      - sk- + 20 位以上
      - Bearer + token
      - api_key / deepseek_api_key / token / authorization 等键值对
    输出中不得包含真实 API Key、Authorization、Bearer、token 原文。
    """
    import re

    patterns: list[tuple[str, str]] = [
        (r"sk-[a-zA-Z0-9]{20,}", "[REDACTED]"),
        (r"Bearer\s+[a-zA-Z0-9\-_\.]+", "[REDACTED]"),
        (r'(?i)api_key[=:]\s*[^\s,"\']+', "[REDACTED]"),
        (r'(?i)deepseek_api_key[=:]\s*[^\s,"\']+', "[REDACTED]"),
        (r'(?i)token[=:]\s*[^\s,"\']+', "[REDACTED]"),
        (r'(?i)authorization[=:]\s*[^\s,"\']+', "[REDACTED]"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


# ═══════════════════════════════════════════════════════════════════════
# Provider 接口
# ═══════════════════════════════════════════════════════════════════════

class BaseProvider:
    """LLM Provider 基类"""

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: int = 20,
        **kwargs: Any,
    ) -> LLMResponse:
        raise NotImplementedError


# ── DeepSeekProvider ───────────────────────────────────────────────────

class DeepSeekProvider(BaseProvider):
    """DeepSeek API Provider —— 复用 backend/app/llm/deepseek.py"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url or settings.DEEPSEEK_BASE_URL
        self.api_key = api_key or settings.DEEPSEEK_API_KEY

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: int = 20,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 DeepSeek API，复用现有 chat_with_llm

        注意：不直接调用 chat_with_llm（它是流式 generator），
        而是使用相同的 httpx 非流式调用模式以获取完整响应。
        """
        model = model or settings.LLM_MODEL

        if not self.api_key:
            return _fail_response("DeepSeek API Key 未配置，已回退到规则流程", "deepseek", model)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            timeout_cfg = httpx.Timeout(float(timeout), connect=10.0)
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return LLMResponse(
                    ok=True,
                    content=content,
                    provider="deepseek",
                    model=model,
                    fallback_used=False,
                )

        except httpx.TimeoutException:
            logger.error("DeepSeek API 请求超时")
            return _fail_response("LLM 请求超时，已回退到规则流程", "deepseek", model)
        except httpx.HTTPStatusError as e:
            logger.error(f"DeepSeek API HTTP {e.response.status_code}")
            return _fail_response(
                f"LLM 服务异常 (HTTP {e.response.status_code})，已回退到规则流程",
                "deepseek", model,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"DeepSeek 响应解析失败: {e}")
            return _fail_response("LLM 响应格式异常，已回退到规则流程", "deepseek", model)
        except Exception as e:
            logger.exception(f"DeepSeek API 调用异常: {e}")
            return _fail_response(
                f"LLM 调用失败，已回退到规则流程",
                "deepseek", model,
            )


# ── LocalOpenAICompatibleProvider ──────────────────────────────────────

class LocalOpenAICompatibleProvider(BaseProvider):
    """本地 OpenAI-compatible 模型 Provider（预留接口）

    使用 OpenAI-compatible /chat/completions 协议，兼容：
      - vLLM
      - Ollama OpenAI API
      - LM Studio
      - 其他 OpenAI-compatible 服务

    当前测试阶段使用 mock，不调用真实本地服务。
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = base_url or settings.LOCAL_LLM_BASE_URL
        self.api_key = api_key or settings.LOCAL_LLM_API_KEY
        self.default_model = model or settings.LOCAL_LLM_MODEL

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: int = 20,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用本地 OpenAI-compatible API"""
        model = model or self.default_model

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            timeout_cfg = httpx.Timeout(float(timeout), connect=10.0)
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return LLMResponse(
                    ok=True,
                    content=content,
                    provider="local_openai_compatible",
                    model=model,
                    fallback_used=False,
                )

        except httpx.TimeoutException:
            logger.error("本地 LLM 请求超时")
            return _fail_response("本地 LLM 请求超时，已回退到规则流程", "local_openai_compatible", model)
        except httpx.ConnectError:
            logger.error("本地 LLM 连接失败")
            return _fail_response("本地 LLM 服务未就绪，已回退到规则流程", "local_openai_compatible", model)
        except httpx.HTTPStatusError as e:
            logger.error(f"本地 LLM HTTP {e.response.status_code}")
            return _fail_response(
                f"本地 LLM 服务异常 (HTTP {e.response.status_code})，已回退到规则流程",
                "local_openai_compatible", model,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"本地 LLM 响应解析失败: {e}")
            return _fail_response("本地 LLM 响应格式异常，已回退到规则流程", "local_openai_compatible", model)
        except Exception as e:
            logger.exception(f"本地 LLM 调用异常: {e}")
            return _fail_response(
                "本地 LLM 调用失败，已回退到规则流程",
                "local_openai_compatible", model,
            )


# ═══════════════════════════════════════════════════════════════════════
# LLMClient —— 统一入口
# ═══════════════════════════════════════════════════════════════════════

class LLMClient:
    """统一 LLM 调用客户端

    使用方式：
        client = LLMClient()
        resp = await client.chat([{"role": "user", "content": "你好"}])
        if resp.ok:
            print(resp.content)
        else:
            # 自动 fallback 到规则流程
            print(f"LLM 不可用: {resp.error}")
    """

    def __init__(self, provider: BaseProvider | None = None) -> None:
        """
        参数:
            provider: 可选注入 provider（用于测试 mock）
                     未传入时根据 settings.LLM_PROVIDER 自动选择
        """
        self._provider: BaseProvider | None = provider
        self._provider_name: str = ""

    def _get_provider(self) -> BaseProvider:
        """按配置获取 provider 实例（惰性初始化）"""
        if self._provider is not None:
            return self._provider

        provider_name = settings.LLM_PROVIDER

        if provider_name == "deepseek":
            self._provider = DeepSeekProvider()
            self._provider_name = "deepseek"
        elif provider_name in ("local_openai_compatible", "openai_compatible"):
            self._provider = LocalOpenAICompatibleProvider()
            self._provider_name = "local_openai_compatible"
        else:
            logger.warning(f"未知 LLM_PROVIDER: {provider_name}，回退到 deepseek")
            self._provider = DeepSeekProvider()
            self._provider_name = "deepseek"

        return self._provider

    # ── 主调用入口 ──────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """统一 LLM 调用入口

        参数:
            messages: 对话消息列表
            model: 模型名，默认从配置读取
            temperature: 温度参数，默认从配置读取
            max_tokens: 最大 token 数，默认从配置读取
            timeout: 超时秒数，默认从配置读取

        返回:
            LLMResponse —— 不会抛异常，所有错误都封装在返回结构中
        """
        # ── 检查 LLM 是否启用 ──
        if not settings.LLM_ENABLED:
            return LLMResponse(
                ok=False,
                content="",
                error="LLM 未启用，已回退到规则流程",
                provider=settings.LLM_PROVIDER,
                model=model or settings.LLM_MODEL,
                fallback_used=True,
            )

        # ── 获取 provider ──
        try:
            provider = self._get_provider()
        except Exception as e:
            logger.exception(f"LLMClient provider 初始化失败: {e}")
            return _fail_response(
                "LLM 服务初始化失败，已回退到规则流程",
                settings.LLM_PROVIDER,
                model or settings.LLM_MODEL,
            )

        # ── 填充默认参数 ──
        model = model or settings.LLM_MODEL
        temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
        max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS
        timeout = timeout if timeout is not None else settings.LLM_TIMEOUT

        # ── 调用 provider ──
        try:
            return await provider.chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                **kwargs,
            )
        except Exception as e:
            logger.exception(f"LLMClient 调用异常: {e}")
            return _fail_response(
                "LLM 调用异常，已回退到规则流程",
                settings.LLM_PROVIDER,
                model,
            )

    # ── 便捷方法 ──────────────────────────────────────────────────────

    async def chat_simple(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> LLMResponse:
        """简化调用：自动构造 messages 列表"""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return await self.chat(messages, **kwargs)

    @property
    def is_enabled(self) -> bool:
        """LLM 是否已启用"""
        return settings.LLM_ENABLED

    @property
    def provider_name(self) -> str:
        """当前 provider 名称"""
        if self._provider is not None:
            return self._provider_name
        return settings.LLM_PROVIDER

    @property
    def model_name(self) -> str:
        """当前模型名称"""
        return settings.LLM_MODEL


# ── 模块级便捷函数 ────────────────────────────────────────────────────

async def llm_chat(
    messages: list[dict[str, str]],
    model: str = "",
    **kwargs: Any,
) -> LLMResponse:
    """模块级便捷函数 —— 创建临时 LLMClient 并调用"""
    client = LLMClient()
    return await client.chat(messages, model=model, **kwargs)
