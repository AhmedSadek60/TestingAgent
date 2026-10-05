"""Provider registry and manager. Adding a provider means registering an adapter class."""

from __future__ import annotations

from agentlab.core.config import AgentLabConfig, ProviderConfig
from agentlab.core.errors import UserError
from agentlab.core.plugins import Registry
from agentlab.providers.base import Capability, LLMProvider, ModelInfo
from agentlab.providers.gemini import GeminiProvider
from agentlab.providers.mock import MockProvider
from agentlab.providers.ollama import OllamaProvider
from agentlab.providers.openai_compat import (
    LlamaCppProvider,
    LMStudioProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    OpenRouterProvider,
    VLLMProvider,
)
from agentlab.security.credentials import CredentialManager

PROVIDER_TYPES: Registry[type[LLMProvider]] = Registry("providers")


def _register_builtins() -> None:
    items: dict[str, type[LLMProvider]] = {
        "mock": MockProvider,
        "openai": OpenAIProvider,
        "openai_compatible": OpenAICompatibleProvider,
        "openrouter": OpenRouterProvider,
        "gemini": GeminiProvider,
        "ollama": OllamaProvider,
        "lmstudio": LMStudioProvider,
        "vllm": VLLMProvider,
        "llamacpp": LlamaCppProvider,
    }
    try:
        from agentlab.providers.anthropic_provider import AnthropicProvider

        items["anthropic"] = AnthropicProvider
    except Exception:  # pragma: no cover - module import never fails without the SDK
        pass
    for k, v in items.items():
        PROVIDER_TYPES.register(k, v, replace=True)


_register_builtins()


def create_provider(config: ProviderConfig, credentials: CredentialManager | None = None) -> LLMProvider:
    try:
        cls = PROVIDER_TYPES.get(config.type)
    except KeyError as exc:
        raise UserError(str(exc)) from exc
    return cls(config, credentials)


class ProviderManager:
    """Owns configured provider instances (lazily created) and answers capability queries."""

    def __init__(self, config: AgentLabConfig, credentials: CredentialManager | None = None) -> None:
        self.config = config
        self.credentials = credentials
        self._instances: dict[str, LLMProvider] = {}
        self._overrides: dict[str, LLMProvider] = {}

    def register_instance(self, name: str, provider: LLMProvider) -> None:
        """Inject a ready provider (used by tests and embedding applications)."""
        self._overrides[name] = provider

    def names(self) -> list[str]:
        return sorted({p.name for p in self.config.providers} | set(self._overrides))

    def get(self, name: str) -> LLMProvider:
        if name in self._overrides:
            return self._overrides[name]
        if name not in self._instances:
            self._instances[name] = create_provider(self.config.provider(name), self.credentials)
        return self._instances[name]

    def describe(self) -> list[dict[str, object]]:
        out = []
        for p in self.config.providers:
            try:
                inst = self.get(p.name)
                caps = sorted(c.value for c in inst.capabilities(p.model))
                err = None
            except Exception as exc:
                caps, err = [], str(exc)
            out.append({"name": p.name, "type": p.type, "base_url": p.base_url, "model": p.model,
                        "capabilities": caps, "configured_error": err,
                        "api_key_ref": p.api_key_ref})  # reference only, never a value
        return out

    async def discover_models(self, name: str) -> list[ModelInfo]:
        return await self.get(name).discover()

    def supports(self, name: str, cap: Capability, model: str | None = None) -> bool:
        return self.get(name).supports(cap, model)

    async def aclose(self) -> None:
        for inst in [*self._instances.values(), *self._overrides.values()]:
            await inst.aclose()
