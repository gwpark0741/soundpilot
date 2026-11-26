"""Process-global GPU server connection override set from the UI.

One token protects both the inference server and the Audyssey tools server.
An empty override falls back to the environment settings.
"""

from __future__ import annotations

import os
import threading

from v2a_inspect.client.config.settings import settings as client_settings
from v2a_inspect.config.settings import get_settings

_lock = threading.Lock()
_override: dict[str, str | None] = {
    "server_url": None,
    "tools_url": None,
    "token": None,
}


def set_connection(
    server_url: str | None, tools_url: str | None, token: str | None
) -> None:
    with _lock:
        _override["server_url"] = server_url or None
        _override["tools_url"] = tools_url or None
        _override["token"] = token or None


def override_token() -> str | None:
    """The UI-set token only (no env fallback); lets the route keep it on token=null."""
    with _lock:
        return _override["token"]


def inference_url() -> str | None:
    with _lock:
        url = _override["server_url"]
    return url or os.getenv("V2A_INSPECT_UI_SERVER_URL") or None


def inference_token() -> str:
    with _lock:
        token = _override["token"]
    return token or client_settings.server_token


def tools_url() -> str:
    with _lock:
        url = _override["tools_url"]
    return (url or get_settings().audyssey_tools_url).rstrip("/")


def tools_token() -> str | None:
    with _lock:
        token = _override["token"]
    if token:
        return token
    secret = get_settings().audyssey_tool_token
    return secret.get_secret_value() if secret else None


def token_set() -> bool:
    return bool(inference_token() or tools_token())
