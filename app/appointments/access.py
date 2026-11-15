"""Opaque credential from application wiring. Roles/identities come from SQLite."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AccessContext:
    session_token: str | None = field(default=None, repr=False)
