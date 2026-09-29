"""AI provider abstraction.

The planning engine never depends on a language model: all game calculations
are local and deterministic. Providers are optional helpers used only on
demand for (a) polishing explanations, (b) interpreting an ambiguous
screenshot, (c) post-match commentary.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger("gcoach.providers")

SYSTEM_COACH = (
    "Ты — тренер по карточной игре «Священный призыв семерых» (Genius Invokation TCG) из Genshin Impact. "
    "Отвечай по-русски, кратко и конкретно. Опирайся только на переданные данные: движок правил уже "
    "просчитал варианты, не выдумывай скрытые карты или числа, которых нет во входных данных."
)


class ProviderError(RuntimeError):
    pass


class AIProvider(ABC):
    name = "base"

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def explain(self, context: dict[str, Any]) -> str:
        """Rewrite a computed recommendation as a short coaching explanation."""

    @abstractmethod
    def post_match(self, report: dict[str, Any]) -> str:
        """Short narrative commentary for the post-match report."""

    def interpret_screenshot(self, png: bytes, question: str) -> dict[str, Any]:
        """Optional: read hard-to-recognise UI elements. Results are INFERRED, never KNOWN."""
        raise ProviderError(f"{self.name}: screenshot interpretation is not supported")


class MockProvider(AIProvider):
    """Deterministic offline provider (default). Useful for tests and for playing without network."""

    name = "mock"

    def explain(self, context: dict[str, Any]) -> str:
        rec = context.get("recommendation", {})
        why = "; ".join(rec.get("why", [])[:3])
        plan = " → ".join(step["label"] for step in rec.get("future_plan", [])[:3])
        text = f"Рекомендую: {rec.get('title', '—')}. {why}."
        if plan:
            text += f" Дальше по плану: {plan}."
        risk = (rec.get("risk") or [""])[0]
        if risk:
            text += f" Риск: {risk}."
        return text

    def post_match(self, report: dict[str, Any]) -> str:
        n_mistakes = len(report.get("mistakes", []))
        return (f"Итог: {report.get('result')}. Ошибок по оценке движка: {n_mistakes}. "
                + " ".join(report.get("future_improvements", [])[:2]))


class ClaudeProvider(AIProvider):
    name = "claude"

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "low", timeout: float = 30):
        import anthropic  # optional dependency

        self._anthropic = anthropic
        self.model = model
        self.effort = effort
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=1)

    def _ask(self, content: list[dict[str, Any]] | str, max_tokens: int = 16000) -> str:
        a = self._anthropic
        try:
            # Server-side fallback: if the request is declined, the API retries on a fallback model.
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=SYSTEM_COACH,
                messages=[{"role": "user", "content": content}],
            )
        except a.AuthenticationError as exc:
            raise ProviderError("Claude: нет ключа API (ANTHROPIC_API_KEY) или он неверен") from exc
        except a.RateLimitError as exc:
            raise ProviderError("Claude: превышен лимит запросов, попробуйте позже") from exc
        except a.APITimeoutError as exc:
            raise ProviderError("Claude: превышено время ожидания (AI_TIMEOUT)") from exc
        except a.APIStatusError as exc:
            raise ProviderError(f"Claude: ошибка API {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise ProviderError("Claude: нет соединения с API") from exc
        if resp.stop_reason == "refusal":
            raise ProviderError("Claude отклонил запрос")
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def explain(self, context: dict[str, Any]) -> str:
        payload = json.dumps(context, ensure_ascii=False)[:20000]
        return self._ask(
            "Вот расчёт движка для текущей позиции (JSON). Объясни игроку в 3-5 предложениях, почему "
            "рекомендованное действие лучше альтернатив и что делать дальше.\n\n" + payload)

    def post_match(self, report: dict[str, Any]) -> str:
        payload = json.dumps(report, ensure_ascii=False)[:30000]
        return self._ask("Разбор партии (JSON). Напиши короткий комментарий тренера: 3 главных вывода "
                         "и 2 конкретных совета на следующую игру.\n\n" + payload)

    def interpret_screenshot(self, png: bytes, question: str) -> dict[str, Any]:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png).decode("ascii")}},
            {"type": "text", "text": question + "\nОтветь только JSON-объектом без пояснений."},
        ]
        text = self._ask(content, max_tokens=4000)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ProviderError("Claude вернул не-JSON ответ")
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError("Claude вернул некорректный JSON") from exc


class LocalProvider(AIProvider):
    """Local model served by Ollama (http://127.0.0.1:11434) - nothing leaves the machine."""

    name = "local"

    def __init__(self, url: str = "http://127.0.0.1:11434", model: str = "llama3.1", timeout: float = 30):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def available(self) -> bool:
        try:
            urllib.request.urlopen(self.url + "/api/tags", timeout=1.5)
            return True
        except Exception:
            return False

    def _ask(self, prompt: str) -> str:
        body = json.dumps({"model": self.model, "prompt": prompt, "system": SYSTEM_COACH, "stream": False}).encode()
        req = urllib.request.Request(self.url + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8")).get("response", "").strip()
        except Exception as exc:
            raise ProviderError(f"Локальная модель недоступна: {exc}") from exc

    def explain(self, context: dict[str, Any]) -> str:
        return self._ask("Объясни рекомендацию движка кратко (3-5 предложений):\n"
                         + json.dumps(context, ensure_ascii=False)[:8000])

    def post_match(self, report: dict[str, Any]) -> str:
        return self._ask("Кратко прокомментируй разбор партии:\n" + json.dumps(report, ensure_ascii=False)[:8000])


def make_provider(cfg) -> AIProvider:
    kind = (cfg.AI_PROVIDER or "mock").lower()
    try:
        if kind == "claude":
            return ClaudeProvider(cfg.AI_MODEL, cfg.AI_EFFORT, cfg.AI_TIMEOUT)
        if kind == "local":
            return LocalProvider(cfg.LOCAL_URL, cfg.LOCAL_MODEL, cfg.AI_TIMEOUT)
    except Exception as exc:  # missing SDK etc. -> safe offline fallback
        log.warning("AI provider %s unavailable (%s), using mock", kind, exc)
    return MockProvider()
