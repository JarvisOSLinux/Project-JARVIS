"""
Session data class — a Pythonic view of the contextor session record.

The Rust contextor is the source of truth; this dataclass is just a
convenience wrapper for Python code so we're not juggling dicts.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Session:
    """A chat session (analogous to a Claude Desktop conversation)."""

    id: str
    title: str = ""
    created_at: str = ""   # ISO-8601 from the binary
    updated_at: str = ""   # ISO-8601 from the binary
    summary: str = ""      # Rolling summary of older turns (Tier 2)
    metadata: Dict[str, Any] = field(default_factory=dict)
    entry_count: Optional[int] = None  # Populated by list_sessions

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """Build a Session from the contextor's JSON response."""
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            summary=data.get("summary", ""),
            metadata=data.get("metadata") or {},
            entry_count=data.get("entry_count"),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "metadata": self.metadata,
        }
        if self.entry_count is not None:
            out["entry_count"] = self.entry_count
        return out

    def short_id(self) -> str:
        """First 8 chars of the UUID, for display."""
        return self.id[:8] if self.id else "?"

    def display_label(self) -> str:
        """Human-readable one-liner for UIs."""
        label = self.title or f"Chat {self.short_id()}"
        if self.entry_count is not None:
            label += f" ({self.entry_count})"
        return label
