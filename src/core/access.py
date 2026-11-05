"""Role-based access to documents (P2). Scopes are set by the server, never by request bodies."""

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from src.core.enums import AccessLevel
from src.core.models import LegalChunk
from src.core.search import Contract

Role = Literal["employee", "manager", "legal"]
ROLES: tuple[Role, ...] = ("employee", "manager", "legal")

AccessScope = frozenset[AccessLevel]
PUBLIC_ONLY: AccessScope = frozenset({AccessLevel.PUBLIC})
FULL_ACCESS: AccessScope = frozenset(AccessLevel)

# Matrix agreed in PROJECT_PLAN (2026-11-05). configs/*.yaml `access_control` must match it.
DEFAULT_ROLE_ACCESS: dict[Role, AccessScope] = {
    "employee": PUBLIC_ONLY,
    "manager": frozenset({AccessLevel.PUBLIC, AccessLevel.INTERNAL}),
    "legal": FULL_ACCESS,
}


def chunk_access(chunk: LegalChunk) -> AccessLevel:
    """Releases before P1 carry no access field and contain only public law."""
    return chunk.access_level or AccessLevel.PUBLIC


def allows(scope: AccessScope, chunk: LegalChunk) -> bool:
    """True when the caller's scope covers this chunk."""
    return chunk_access(chunk) in scope


class AccessPolicy(Contract):
    """Role -> access levels; requests without credentials get `anonymous`."""

    roles: dict[Role, AccessScope] = Field(default_factory=lambda: dict(DEFAULT_ROLE_ACCESS))
    anonymous: AccessScope = PUBLIC_ONLY

    @field_validator("roles", mode="before")
    @classmethod
    def _as_sets(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {role: frozenset(levels) for role, levels in value.items()}
        return value

    @model_validator(mode="after")
    def _sane(self) -> "AccessPolicy":
        if set(self.roles) != set(ROLES):
            raise ValueError("access_control.roles must define every role")
        for scope in [*self.roles.values(), self.anonymous]:
            if AccessLevel.PUBLIC not in scope:
                raise ValueError("Every scope must include public")
        if not self.anonymous <= self.roles["employee"]:
            raise ValueError("Anonymous access must not exceed the employee role")
        return self

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "AccessPolicy":
        """Read `access_control` from pipeline YAML; absent section means the default matrix."""
        section = config.get("access_control") or {}
        return cls.model_validate(section)
