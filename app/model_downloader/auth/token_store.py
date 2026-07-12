"""On-disk OAuth token persistence — one ``0600`` JSON file per provider.

Tokens live under ``folder_paths.get_system_user_directory("download_auth")``,
never in the SQLite DB. The file is written with ``0600`` so only the owner can
read the refresh/access token at rest.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass

import folder_paths


@dataclass
class Token:
    access_token: str
    refresh_token: str | None = None
    # Epoch seconds when the access token expires; 0 means "unknown / no expiry".
    expires_at: int = 0
    token_type: str = "Bearer"
    scope: str | None = None

    def is_expired(self, skew: int = 60) -> bool:
        if not self.expires_at:
            return False
        return time.time() + skew >= self.expires_at


def _auth_dir() -> str:
    path = folder_paths.get_system_user_directory("download_auth")
    os.makedirs(path, exist_ok=True)
    return path


def _token_path(provider: str) -> str:
    return os.path.join(_auth_dir(), f"{provider}.json")


def load(provider: str) -> Token | None:
    try:
        with open(_token_path(provider), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        return None
    return Token(
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token"),
        expires_at=int(data.get("expires_at", 0) or 0),
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope"),
    )


def save(provider: str, token: Token) -> None:
    path = _token_path(provider)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(token), f)
    os.chmod(path, 0o600)


def delete(provider: str) -> None:
    try:
        os.remove(_token_path(provider))
    except FileNotFoundError:
        pass
