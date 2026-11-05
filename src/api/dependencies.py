"""Shared request-scoped helpers and the service container owned by the app lifespan."""

import hmac
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import Request
from pydantic import SecretStr

from src.common.config import AppSettings
from src.core.access import AccessPolicy, AccessScope, Role
from src.core.api import AskPort, SourcePort
from src.core.search_ports import SearchPort

REQUEST_ID_HEADER = "X-Request-ID"
API_KEY_HEADER = "X-API-Key"


class Unauthorized(Exception):
    """A credential was sent but matches no role; never downgraded to anonymous."""


class AccessResolver:
    """Map the X-API-Key header to a role scope; no header means the anonymous scope."""

    def __init__(
        self,
        policy: AccessPolicy | None = None,
        keys: Mapping[Role, SecretStr | None] | None = None,
    ) -> None:
        self.policy = policy or AccessPolicy()
        active = {role: k.get_secret_value() for role, k in (keys or {}).items() if k is not None}
        self._keys = {role: key for role, key in active.items() if key.strip()}
        if any(len(key) < 16 for key in self._keys.values()):
            raise ValueError("Role API keys must be at least 16 characters")
        if len(set(self._keys.values())) != len(self._keys):
            raise ValueError("Each role needs a distinct API key")

    @classmethod
    def from_settings(cls, policy: AccessPolicy, settings: AppSettings) -> "AccessResolver":
        return cls(
            policy,
            {
                "employee": settings.auth_api_key_employee,
                "manager": settings.auth_api_key_manager,
                "legal": settings.auth_api_key_legal,
            },
        )

    @property
    def configured_roles(self) -> list[Role]:
        return sorted(self._keys)

    def resolve(self, credential: str | None) -> tuple[Role | None, AccessScope]:
        """Compare against every key in constant time; the credential is never logged."""
        if credential is None or credential == "":
            return None, self.policy.anonymous
        supplied = credential.encode()
        matched: Role | None = None
        for role, key in self._keys.items():
            if hmac.compare_digest(supplied, key.encode()):
                matched = role
        if matched is None:
            raise Unauthorized()
        return matched, self.policy.roles[matched]


@dataclass(frozen=True)
class ApiServices:
    """Everything a route may call; `ask` is None when no LLM is configured."""

    search: SearchPort
    sources: SourcePort
    ask: AskPort | None = None
    access: AccessResolver = field(default_factory=AccessResolver)


ServicesOpener = Callable[[], AbstractAsyncContextManager[ApiServices]]


def request_id_of(request: Request) -> UUID:
    """The server-generated ID; client-supplied IDs are never trusted."""
    rid = getattr(request.state, "request_id", None)
    if isinstance(rid, UUID):
        return rid
    rid = uuid4()
    request.state.request_id = rid
    return rid


def services_of(request: Request) -> ApiServices:
    """Services exist only after startup; routes never construct them."""
    services: ApiServices = request.app.state.services
    return services


def access_of(request: Request) -> AccessScope:
    """Scope of the caller; raises Unauthorized for an unknown credential."""
    _, scope = services_of(request).access.resolve(request.headers.get(API_KEY_HEADER))
    return scope
