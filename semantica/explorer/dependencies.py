"""
Semantica Explorer : FastAPI Dependencies

Provides ``Depends()``-compatible callables for injecting the
current ``GraphSession`` into route handlers, and for enforcing API-key
authentication on protected routes. WebSocket manager access is handled
directly via ``app.state.ws_manager``.
"""

import hmac
import os
import time
from typing import Optional

from fastapi import Request, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

from ..context.agent_memory import AgentMemory
from .markdown_resources import MarkdownResourceRegistry
from .session import GraphSession

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
SESSION_COOKIE = "semantica_session"


class LoginSessions:
    """Process-local password sessions. A restart signs everyone out."""

    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}

    def put(self, token: str, username: str, expires_at: float) -> None:
        self._tokens[token] = (username, expires_at)

    def drop(self, token: str) -> None:
        self._tokens.pop(token, None)

    def username(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        row = self._tokens.get(token)
        if row is None:
            return None
        username, expires_at = row
        if expires_at <= time.time():
            self._tokens.pop(token, None)
            return None
        return username


def get_expected_api_key() -> Optional[str]:
    """Read the configured API key from the environment on every call.

    Read fresh (not cached) so tests and ops tooling can rotate the key
    without restarting the process.
    """
    return os.environ.get("SEMANTICA_API_KEY") or None


def anonymous_access_allowed() -> bool:
    return os.environ.get("SEMANTICA_ALLOW_ANONYMOUS", "").strip().lower() == "true"


def login_realm() -> str:
    return os.environ.get("SEMANTICA_LOGIN_REALM", "").strip()


def login_user() -> str:
    return (os.environ.get("SEMANTICA_LOGIN_USER") or "zndx").strip()


def spnego_keytab() -> str:
    return os.environ.get("SEMANTICA_SPNEGO_KEYTAB", "").strip()


def authentication_configured() -> bool:
    """True when an API key or a password-login realm is set."""
    return bool(get_expected_api_key()) or bool(login_realm())


def is_valid_api_key(candidate: Optional[str]) -> bool:
    """Return True if *candidate* matches the configured key, or if the
    server has explicitly opted into anonymous access."""
    if anonymous_access_allowed():
        return True
    expected = get_expected_api_key()
    if not expected:
        return False
    return bool(candidate) and hmac.compare_digest(candidate, expected)


def session_username(request: Request) -> Optional[str]:
    sessions = getattr(request.app.state, "login_sessions", None)
    if sessions is None:
        return None
    return sessions.username(request.cookies.get(SESSION_COOKIE))


def _spnego_username(request: Request) -> Optional[str]:
    from .login import accept_spnego

    return accept_spnego(request.headers.get("authorization"))


def client_authenticated(request: Request, api_key: Optional[str] = None) -> bool:
    """True for anonymous opt-in, a matching API key, a login session, or SPNEGO."""
    if anonymous_access_allowed() or is_valid_api_key(api_key):
        return True
    if session_username(request):
        return True
    return _spnego_username(request) is not None


def require_auth(
    request: Request,
    api_key: Optional[str] = Security(_api_key_header),
) -> None:
    """Dependency enforcing auth on protected routes.

    An API key, a password-login session, or a SPNEGO token each suffice.
    SPNEGO is never required. With neither an API key nor ``SEMANTICA_LOGIN_REALM``,
    requests are refused with 503. ``SEMANTICA_ALLOW_ANONYMOUS=true`` opts into
    open access for local development.
    """
    if anonymous_access_allowed():
        return
    if not authentication_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Server is not configured for authentication. Set the "
                "SEMANTICA_API_KEY environment variable, or explicitly opt "
                "into unauthenticated access (development only) with "
                "SEMANTICA_ALLOW_ANONYMOUS=true."
            ),
        )
    if is_valid_api_key(api_key) or session_username(request) or _spnego_username(request):
        return
    headers = {"WWW-Authenticate": "Negotiate"} if spnego_keytab() else None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Sign in. The username is the Kerberos user principal "
            f"({login_user()})."
        ),
        headers=headers,
    )


def get_session(request: Request) -> GraphSession:
    """Retrieve the GraphSession stored on ``app.state``."""
    if not hasattr(request.app.state, "session") or request.app.state.session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GraphSession not initialized."
        )
    return request.app.state.session


def get_markdown_resources(request: Request) -> MarkdownResourceRegistry:
    """Retrieve the Markdown resource registry stored on ``app.state``."""
    resources = getattr(request.app.state, "markdown_resources", None)
    if resources is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Markdown resources are not initialized.",
        )
    return resources


def get_agent_memory(request: Request) -> AgentMemory:
    """Retrieve the optional AgentMemory configured for Explorer."""
    memory = getattr(request.app.state, "agent_memory", None)
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AgentMemory is not configured for this Explorer instance.",
        )
    return memory
