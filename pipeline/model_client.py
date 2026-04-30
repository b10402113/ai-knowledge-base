"""Unified LLM client for multiple OpenAI-compatible providers.

Supports DeepSeek, Qwen (Tongyi Qianwen), and OpenAI via their
OpenAI-compatible chat completion endpoints using httpx directly.

Configuration is driven by environment variables:
    LLM_PROVIDER  – "deepseek" | "qwen" | "openai"  (default: "deepseek")
    DEEPSEEK_API_KEY / QWEN_API_KEY / OPENAI_API_KEY – provider API key

Usage:
    from pipeline.model_client import quick_chat

    reply = quick_chat("Explain transformers in one paragraph.")
"""

import abc
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 3
BACKOFF_BASE = 2

RMB_PRICES: Dict[str, Dict[str, float]] = {
    "deepseek": {"input": 1.0, "output": 2.0},
    "qwen": {"input": 4.0, "output": 12.0},
    "openai": {"input": 150.0, "output": 600.0},
}


@dataclass
class Usage:
    """Token usage statistics returned by the API.

    Attributes:
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        total_tokens: Total tokens (prompt + completion).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CostTracker:
    """追踪 LLM 调用的 token 消耗和成本（人民币）。

    按提供商分组记录每次 API 调用的 token 使用情况，
    并提供成本估算和汇总报告功能。

    Attributes:
        _records: 按 provider 名称分组的调用记录列表。
    """

    def __init__(self) -> None:
        """初始化 CostTracker。"""
        self._records: Dict[str, List[Dict[str, Any]]] = {}

    def record(self, usage: Usage, provider: str) -> None:
        """记录一次 API 调用的 token 使用情况。

        Args:
            usage: 本次调用的 token 使用统计。
            provider: 提供商名称（如 "deepseek", "qwen", "openai"）。
        """
        provider = provider.lower()
        if provider not in self._records:
            self._records[provider] = []

        self._records[provider].append({
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "timestamp": time.time(),
        })

        logger.debug(
            "Recorded usage for %s: %d prompt + %d completion tokens",
            provider,
            usage.prompt_tokens,
            usage.completion_tokens,
        )

    def estimated_cost(self, provider: str) -> float:
        """计算指定提供商的累计成本（元）。

        Args:
            provider: 提供商名称。

        Returns:
            累计成本（人民币元）。

        Raises:
            ValueError: 如果提供商不在价格表中。
        """
        provider = provider.lower()
        if provider not in RMB_PRICES:
            raise ValueError(
                f"Unknown provider '{provider}'. Available: {list(RMB_PRICES.keys())}"
            )

        if provider not in self._records:
            return 0.0

        prices = RMB_PRICES[provider]
        total_input = sum(r["prompt_tokens"] for r in self._records[provider])
        total_output = sum(r["completion_tokens"] for r in self._records[provider])

        cost = (
            total_input * prices["input"] + total_output * prices["output"]
        ) / 1_000_000

        return cost

    def report(self, provider: Optional[str] = None) -> None:
        """打印成本报告。

        如果指定 provider，只打印该提供商的报告；
        否则打印所有提供商的汇总报告。

        Args:
            provider: 可选的提供商名称。为 None 时打印全部。
        """
        if not self._records:
            print("📊 CostTracker Report: No API calls recorded.")
            return

        print("\n" + "=" * 60)
        print("📊 CostTracker Report")
        print("=" * 60)

        providers_to_report = (
            [provider.lower()] if provider else list(self._records.keys())
        )

        total_cost_all = 0.0

        for p in providers_to_report:
            if p not in self._records:
                print(f"\n  [{p}] No records found.")
                continue

            records = self._records[p]
            total_calls = len(records)
            total_input = sum(r["prompt_tokens"] for r in records)
            total_output = sum(r["completion_tokens"] for r in records)
            total_tokens = total_input + total_output

            try:
                cost = self.estimated_cost(p)
                total_cost_all += cost
            except ValueError:
                cost = 0.0

            print(f"\n  [{p.upper()}]")
            print(f"    Calls:          {total_calls}")
            print(f"    Input tokens:   {total_input:,}")
            print(f"    Output tokens:  {total_output:,}")
            print(f"    Total tokens:   {total_tokens:,}")
            print(f"    Estimated cost: ¥{cost:.4f}")

        print("\n" + "-" * 60)
        print(f"  💰 Total estimated cost: ¥{total_cost_all:.4f}")
        print("=" * 60 + "\n")

    def reset(self) -> None:
        """清空所有记录。"""
        self._records.clear()
        logger.debug("CostTracker records cleared.")


tracker = CostTracker()


@dataclass
class LLMResponse:
    """Unified response from any LLM provider.

    Attributes:
        content: The text content of the assistant's reply.
        usage: Token usage statistics.
        model: The model name used for the request.
    """

    content: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider.

    Attributes:
        name: Human-readable provider name.
        base_url: API base URL for chat completions.
        api_key_env: Environment variable name holding the API key.
        default_model: Default model identifier.
        price_input: USD cost per 1M input tokens.
        price_output: USD cost per 1M output tokens.
    """

    name: str
    base_url: str
    api_key_env: str
    default_model: str
    price_input: float
    price_output: float


PROVIDERS: Dict[str, ProviderConfig] = {
    "deepseek": ProviderConfig(
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        price_input=0.14,
        price_output=0.28,
    ),
    "qwen": ProviderConfig(
        name="Qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        default_model="qwen-plus",
        price_input=0.40,
        price_output=1.20,
    ),
    "openai": ProviderConfig(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-4o-mini",
        price_input=0.15,
        price_output=0.60,
    ),
}


class LLMProvider(abc.ABC):
    """Abstract base class defining the LLM provider interface."""

    @abc.abstractmethod
    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = REQUEST_TIMEOUT,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: A list of message dicts with "role" and "content" keys.
            model: Model identifier override. Uses provider default if None.
            temperature: Sampling temperature in [0, 2].
            max_tokens: Maximum tokens in the completion.
            timeout: Request timeout in seconds.

        Returns:
            An LLMResponse with content, usage, and model info.
        """


class OpenAICompatibleProvider(LLMProvider):
    """Provider implementation for OpenAI-compatible chat APIs.

    Works with any service that exposes the ``/chat/completions`` endpoint
    following the OpenAI protocol (DeepSeek, Qwen/DashScope, OpenAI, etc.).
    """

    def __init__(self, config: ProviderConfig, api_key: Optional[str] = None):
        self._config = config
        self._api_key = api_key or os.environ.get(config.api_key_env, "")
        if not self._api_key:
            logger.warning(
                "API key not found for %s (env: %s)",
                config.name,
                config.api_key_env,
            )

    @property
    def config(self) -> ProviderConfig:
        """Return the provider configuration."""
        return self._config

    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = REQUEST_TIMEOUT,
    ) -> LLMResponse:
        """Send a chat completion request via httpx.

        Args:
            messages: A list of message dicts with "role" and "content" keys.
            model: Model identifier override.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the completion.
            timeout: Request timeout in seconds.

        Returns:
            An LLMResponse instance.

        Raises:
            httpx.HTTPStatusError: If the API returns a non-2xx status.
            httpx.TimeoutException: If the request times out.
        """
        model = model or self._config.default_model
        url = f"{self._config.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.debug(
            "Sending chat request to %s (model=%s, messages=%d)",
            self._config.name,
            model,
            len(messages),
        )

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        raw_usage = data.get("usage", {})

        usage = Usage(
            prompt_tokens=raw_usage.get("prompt_tokens", 0),
            completion_tokens=raw_usage.get("completion_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
        )

        logger.info(
            "%s responded: %d prompt + %d completion = %d total tokens",
            self._config.name,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
        )

        response = LLMResponse(content=content, usage=usage, model=model)

        provider_key = next(
            (k for k, v in PROVIDERS.items() if v.name == self._config.name),
            "unknown",
        )
        tracker.record(usage, provider_key)

        return response


def get_provider(name: Optional[str] = None, api_key: Optional[str] = None) -> OpenAICompatibleProvider:
    """Create a provider instance based on the given or environment-configured name.

    Args:
        name: Provider key in ``PROVIDERS``. Falls back to ``LLM_PROVIDER``
            environment variable, then to ``"deepseek"``.
        api_key: Optional API key override. Otherwise read from the provider's
            designated environment variable.

    Returns:
        An initialised ``OpenAICompatibleProvider``.

    Raises:
        ValueError: If the provider name is not recognised.
    """
    name = name or os.environ.get("LLM_PROVIDER", "deepseek")
    name = name.lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown provider '{name}'. Available: {list(PROVIDERS.keys())}"
        )
    config = PROVIDERS[name]
    logger.info("Using LLM provider: %s", config.name)
    return OpenAICompatibleProvider(config, api_key=api_key)


def chat_with_retry(
    messages: Sequence[Dict[str, str]],
    *,
    provider: Optional[OpenAICompatibleProvider] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    max_retries: int = MAX_RETRIES,
    timeout: float = REQUEST_TIMEOUT,
) -> LLMResponse:
    """Send a chat request with automatic retries and exponential back-off.

    Args:
        messages: Chat message list.
        provider: Provider instance. Created via ``get_provider()`` if None.
        model: Model identifier override.
        temperature: Sampling temperature.
        max_tokens: Maximum completion tokens.
        max_retries: Maximum number of retry attempts.
        timeout: Per-request timeout in seconds.

    Returns:
        An LLMResponse on success.

    Raises:
        Exception: The last exception encountered after all retries are exhausted.
    """
    provider = provider or get_provider()
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            return provider.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = BACKOFF_BASE ** attempt
                logger.warning(
                    "Attempt %d/%d failed (%s). Retrying in %ds …",
                    attempt,
                    max_retries,
                    type(exc).__name__,
                    wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "All %d attempts exhausted. Last error: %s",
                    max_retries,
                    exc,
                )

    raise last_exc  # type: ignore[misc]


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in *text* using a heuristic.

    Uses the common approximation of ~4 characters per token for English
    text and ~2 characters per token for Chinese text.

    Args:
        text: The text to estimate.

    Returns:
        Estimated token count.
    """
    chinese_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    non_chinese_chars = len(text) - chinese_chars
    tokens = math.ceil(chinese_chars / 2) + math.ceil(non_chinese_chars / 4)
    return max(tokens, 1)


def calculate_cost(
    usage: Usage,
    provider_name: str = "deepseek",
) -> float:
    """Calculate the approximate USD cost for a given usage and provider.

    Args:
        usage: Token usage statistics.
        provider_name: Provider key in ``PROVIDERS``.

    Returns:
        Estimated cost in USD.

    Raises:
        ValueError: If the provider name is not recognised.
    """
    provider_name = provider_name.lower()
    if provider_name not in PROVIDERS:
        raise ValueError(
            f"Unknown provider '{provider_name}'. Available: {list(PROVIDERS.keys())}"
        )
    config = PROVIDERS[provider_name]
    cost = (
        usage.prompt_tokens * config.price_input
        + usage.completion_tokens * config.price_output
    ) / 1_000_000
    return cost


def quick_chat(
    prompt: str,
    *,
    system: str = "You are a helpful assistant.",
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """One-shot convenience function to get an LLM reply.

    Args:
        prompt: The user message.
        system: System prompt.
        provider_name: Provider key override.
        model: Model identifier override.
        temperature: Sampling temperature.
        max_tokens: Maximum completion tokens.

    Returns:
        The assistant's reply text.
    """
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    provider = get_provider(provider_name)
    response = chat_with_retry(
        messages,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    cost = calculate_cost(response.usage, provider_name or os.environ.get("LLM_PROVIDER", "deepseek"))
    logger.info("quick_chat cost: $%.6f", cost)

    return response.content


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== model_client.py smoke test ===\n")

    for key in ("deepseek", "qwen", "openai"):
        cfg = PROVIDERS[key]
        env_var = cfg.api_key_env
        has_key = bool(os.environ.get(env_var))
        print(f"  {cfg.name:8s}  key={env_var:20s}  set={has_key}")

    print()

    sample = "你好，请用一句话介绍你自己。"
    est = estimate_tokens(sample)
    print(f"  Token estimate for \"{sample}\": ~{est} tokens\n")

    demo_usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    for key in PROVIDERS:
        c = calculate_cost(demo_usage, key)
        print(f"  {PROVIDERS[key].name:8s} cost for {demo_usage}: ${c:.6f}")

    print()

    current = os.environ.get("LLM_PROVIDER", "deepseek")
    if os.environ.get(PROVIDERS[current].api_key_env):
        print(f"  --- Live test with {PROVIDERS[current].name} ---")
        try:
            reply = quick_chat("用一句话解释什么是大语言模型。")
            print(f"  Reply: {reply}\n")
        except Exception as exc:
            print(f"  Error: {exc}\n")
    else:
        print(f"  (Skipping live test: {PROVIDERS[current].api_key_env} not set)\n")

    print("=== done ===")
