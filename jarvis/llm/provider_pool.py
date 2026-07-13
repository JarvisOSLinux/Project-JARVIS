"""Provider pool with automatic failover.

Implements ``BaseLLMProvider`` so the rest of JARVIS (chat.py, LLM)
sees a single provider.  Internally walks a priority-ordered list and
fails over on errors:

- 429 (rate limit)    → cooldown 60 s
- 402 (quota)         → cooldown 1 hour
- 401/403 (auth)      → permanent error
- 5xx / timeout       → cooldown 30 s

Cooled-down providers restore automatically when their timer expires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..core.logger import get_logger
from .base import BaseLLMProvider

logger = get_logger(__name__)

COOLDOWN_RATE_LIMIT = 60
COOLDOWN_QUOTA = 3600
COOLDOWN_SERVER_ERROR = 30
COOLDOWN_NETWORK = 30


@dataclass
class ProviderEntry:
    """A provider in the pool with runtime state."""

    provider: BaseLLMProvider
    name: str
    status: str = "active"
    cooldown_until: Optional[datetime] = None
    failure_count: int = 0
    last_error: Optional[str] = None


class ProviderPool(BaseLLMProvider):
    """Failover pool — tries providers in list order, skipping unavailable ones."""

    def __init__(self, entries: List[ProviderEntry]) -> None:
        if not entries:
            raise ValueError("ProviderPool requires at least one provider")
        super().__init__(entries[0].provider.model)
        self._entries = entries

    def chat(self, messages: List[Dict[str, str]]) -> str:
        now = datetime.now()
        self._restore_cooled_down(now)

        errors: List[tuple[str, str]] = []
        for entry in self._entries:
            if entry.status != "active":
                continue
            try:
                result = entry.provider.chat(messages)
                self.model = entry.provider.model
                self.last_prompt_tokens = entry.provider.last_prompt_tokens
                self.last_completion_tokens = entry.provider.last_completion_tokens
                if entry.failure_count > 0:
                    logger.info(f"Provider '{entry.name}' recovered")
                    entry.failure_count = 0
                return result
            except Exception as e:
                entry.failure_count += 1
                entry.last_error = str(e)
                self._classify_and_mark(entry, e, now)
                errors.append((entry.name, str(e)))
                if len(self._entries) > 1:
                    logger.warning(
                        f"Provider '{entry.name}' failed ({entry.status}), "
                        f"trying next"
                    )

        raise RuntimeError(
            "All LLM providers are unavailable:\n" + self._status_lines_text(now)
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        now = datetime.now()
        self._restore_cooled_down(now)

        errors: List[tuple[str, str]] = []
        unsupported = 0
        for entry in self._entries:
            if entry.status != "active":
                continue
            try:
                result = entry.provider.embed(texts)
                if entry.failure_count > 0:
                    logger.info(f"Provider '{entry.name}' recovered")
                    entry.failure_count = 0
                return result
            except NotImplementedError:
                unsupported += 1
                continue
            except Exception as e:
                entry.failure_count += 1
                entry.last_error = str(e)
                self._classify_and_mark(entry, e, now)
                errors.append((entry.name, str(e)))
                if len(self._entries) > 1:
                    logger.warning(
                        f"Provider '{entry.name}' failed embeddings "
                        f"({entry.status}), trying next"
                    )

        if unsupported and not errors:
            raise NotImplementedError(
                "No active provider in the pool supports embeddings"
            )

        raise RuntimeError(
            "All LLM providers are unavailable for embeddings:\n"
            + self._status_lines_text(now)
        )

    def _status_lines_text(self, now: datetime) -> str:
        status_lines = []
        for entry in self._entries:
            line = f"  {entry.name}: {entry.status}"
            if entry.cooldown_until and entry.status in ("cooldown", "exhausted"):
                remaining = max(0, (entry.cooldown_until - now).total_seconds())
                line += f" (retry in {int(remaining)}s)"
            if entry.last_error:
                line += f" — {entry.last_error}"
            status_lines.append(line)
        return "\n".join(status_lines)

    def is_available(self) -> bool:
        self._restore_cooled_down(datetime.now())
        return any(e.status == "active" for e in self._entries)

    def get_status(self) -> List[Dict[str, Any]]:
        """Status of all providers — for TUI status bar and settings modal."""
        now = datetime.now()
        self._restore_cooled_down(now)
        result = []
        for entry in self._entries:
            info: Dict[str, Any] = {
                "name": entry.name,
                "model": entry.provider.model,
                "status": entry.status,
                "failure_count": entry.failure_count,
            }
            if entry.cooldown_until and entry.status in ("cooldown", "exhausted"):
                remaining = max(0, (entry.cooldown_until - now).total_seconds())
                info["cooldown_remaining"] = int(remaining)
            if entry.last_error:
                info["last_error"] = entry.last_error
            result.append(info)
        return result

    @property
    def active_provider_name(self) -> Optional[str]:
        """Name of the first active provider, or None if all are down."""
        for entry in self._entries:
            if entry.status == "active":
                return entry.name
        return None

    @property
    def entries(self) -> List[ProviderEntry]:
        return list(self._entries)

    def _restore_cooled_down(self, now: datetime) -> None:
        for entry in self._entries:
            if entry.status in ("cooldown", "exhausted") and entry.cooldown_until:
                if now >= entry.cooldown_until:
                    logger.info(f"Provider '{entry.name}' cooldown expired, restoring")
                    entry.status = "active"
                    entry.cooldown_until = None

    def _classify_and_mark(
        self, entry: ProviderEntry, error: Exception, now: datetime
    ) -> None:
        status_code = self._extract_status_code(error)

        if status_code == 429:
            entry.status = "cooldown"
            entry.cooldown_until = now + timedelta(seconds=COOLDOWN_RATE_LIMIT)
        elif status_code == 402:
            entry.status = "exhausted"
            entry.cooldown_until = now + timedelta(seconds=COOLDOWN_QUOTA)
        elif status_code in (401, 403):
            entry.status = "error"
        elif status_code and status_code >= 500:
            entry.status = "cooldown"
            entry.cooldown_until = now + timedelta(seconds=COOLDOWN_SERVER_ERROR)
        else:
            entry.status = "cooldown"
            entry.cooldown_until = now + timedelta(seconds=COOLDOWN_NETWORK)

    @staticmethod
    def _extract_status_code(error: Exception) -> Optional[int]:
        if hasattr(error, "response") and hasattr(error.response, "status_code"):
            return error.response.status_code

        error_str = str(error)
        for code in (429, 402, 401, 403, 500, 502, 503, 504):
            if str(code) in error_str:
                return code

        return None
