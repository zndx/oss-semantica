"""Password login aligned with a Kerberos user principal.

A remote browser signs in with a username and password. The username is the
principal primary (``zndx`` → ``zndx@DEV.VISTA.ZNDX.ORG``). SPNEGO is accepted
when a client sends ``Authorization: Negotiate``, and it is not required.

The warehouse does not use this session. It uses the ``semantica`` service
principal keytab.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import time
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from .dependencies import (
    SESSION_COOKIE,
    LoginSessions,
    login_realm,
    login_user,
    session_username,
    spnego_keytab,
)

logger = logging.getLogger(__name__)

PasswordVerifier = Callable[[str, str], bool]
_SESSION_TTL_S = 12 * 60 * 60


class LoginBody(BaseModel):
    username: str
    password: str


def principal_for(username: str) -> str:
    return f"{username}@{login_realm()}"


def principal_matches(name: str) -> bool:
    """True when *name* is exactly the configured user principal."""
    primary, sep, realm = (name or "").strip().partition("@")
    if not sep or "/" in primary:
        return False
    return primary == login_user() and realm.upper() == login_realm().upper()


def kinit_password(username: str, password: str) -> bool:
    """Return True when the KDC accepts ``username``'s password.

    The check uses a private credential cache and destroys it before returning.
    A wrong password and a missing ``kinit`` both return False.
    """
    import pty
    import subprocess

    if not password:
        return False
    realm = login_realm()
    if not realm or username != login_user():
        return False
    principal = principal_for(username)
    ccache = f"/tmp/semantica-login-{os.getpid()}-{secrets.token_hex(4)}"
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["kinit", "-c", ccache, principal],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            env={**os.environ, "KRB5CCNAME": ccache},
        )
        os.close(slave)
        slave = -1
        os.write(master, password.encode() + b"\n")
        try:
            code = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            return False
        return code == 0
    except (OSError, FileNotFoundError) as exc:
        logger.info("password login could not run kinit: %s", exc)
        return False
    finally:
        if slave >= 0:
            os.close(slave)
        os.close(master)
        subprocess.run(
            ["kdestroy", "-c", ccache],
            check=False,
            capture_output=True,
        )
        try:
            os.remove(ccache)
        except OSError:
            pass


def accept_spnego(authorization: Optional[str]) -> Optional[str]:
    """Return the user primary when *authorization* is a finished Negotiate token.

    Missing ``gssapi``, a missing keytab, or a token for a different principal
    returns None. Callers then fall through to the password session.
    """
    if not authorization or not authorization.lower().startswith("negotiate "):
        return None
    keytab = spnego_keytab()
    host = os.environ.get("SEMANTICA_SPNEGO_HOST", "").strip()
    if not keytab or not host or not login_realm():
        return None
    try:
        import gssapi
    except ImportError:
        return None
    encoded = authorization.split(" ", 1)[1].strip()
    try:
        token = base64.b64decode(encoded)
        name = gssapi.Name(
            f"HTTP/{host}@{login_realm()}",
            name_type=gssapi.NameType.kerberos_principal,
        )
        creds = gssapi.Credentials(usage="accept", name=name, store={"keytab": keytab})
        context = gssapi.SecurityContext(creds=creds, usage="accept")
        context.step(token)
        if not context.complete:
            return None
        client = str(context.initiator_name)
    except Exception as exc:
        logger.info("SPNEGO accept failed: %s", exc)
        return None
    if not principal_matches(client):
        return None
    return login_user()


def install_login_routes(app: FastAPI) -> None:
    """Mount ``/api/login``, ``/api/logout``, and ``/api/session`` without auth."""
    app.state.login_sessions = LoginSessions()

    @app.get("/api/session")
    async def session(request: Request) -> dict:
        username = session_username(request)
        realm = login_realm()
        return {
            "authenticated": username is not None,
            "username": username,
            "login": "password" if realm else None,
            "user": login_user() if realm else None,
        }

    @app.post("/api/login")
    async def login(body: LoginBody, response: Response) -> dict:
        if not login_realm():
            raise HTTPException(
                status_code=503,
                detail="Password login is not configured.",
            )
        verifier: PasswordVerifier = getattr(
            app.state, "password_verifier", kinit_password
        )
        username = body.username.strip()
        if not verifier(username, body.password):
            raise HTTPException(
                status_code=401,
                detail="Invalid username or password.",
            )
        token = secrets.token_urlsafe(32)
        app.state.login_sessions.put(token, username, time.time() + _SESSION_TTL_S)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            max_age=_SESSION_TTL_S,
            path="/",
        )
        return {"username": username}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response) -> dict:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            app.state.login_sessions.drop(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}
