"""Per-installation token. Created once (0600, in a 0700 dir) and required on every request via
X-VoxSync-Token. Checked in the outermost ASGI layer (before any body is read) and again as a route
dependency."""
import hmac
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

from . import config


def ensure_home(path: Path = config.HOME) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def load_or_create_token(path: Path = config.TOKEN_FILE) -> str:
    ensure_home(path.parent)
    if path.exists():
        tok = path.read_text().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    return tok


_token: bytes | None = None


def set_token(tok: str) -> None:
    global _token
    _token = tok.encode()


def token_ok(given: bytes | str | None) -> bool:
    if _token is None or not given:
        return False
    if isinstance(given, str):
        given = given.encode("utf-8", "surrogateescape")
    return hmac.compare_digest(given, _token)


def require_token(x_voxsync_token: str | None = Header(default=None)) -> None:
    if not token_ok(x_voxsync_token):
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
