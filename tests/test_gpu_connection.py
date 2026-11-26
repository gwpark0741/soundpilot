from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from v2a_inspect.audio_generation import audyssey
from v2a_inspect.client.config.settings import settings as client_settings
from v2a_inspect.client.endpoints.base import BaseClient
from v2a_inspect.config import connection
from v2a_inspect.config.settings import get_settings
from v2a_inspect.ui.routes import create_router
from v2a_inspect.ui.store import VideoAssetStore

SECRET = "tok-secret-123"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv("V2A_INSPECT_UI_SERVER_URL", raising=False)
    monkeypatch.setattr(client_settings, "server_token", "")
    monkeypatch.setattr(get_settings(), "audyssey_tool_token", None)
    monkeypatch.setattr(get_settings(), "audyssey_tools_url", "http://127.0.0.1:8700")
    connection.set_connection(None, None, None)
    yield
    connection.set_connection(None, None, None)


def test_override_beats_env(monkeypatch) -> None:
    monkeypatch.setenv("V2A_INSPECT_UI_SERVER_URL", "http://env-inf")
    monkeypatch.setattr(client_settings, "server_token", "env-tok")
    monkeypatch.setattr(get_settings(), "audyssey_tool_token", SecretStr("env-tool"))
    monkeypatch.setattr(get_settings(), "audyssey_tools_url", "http://env-tools")
    assert connection.inference_url() == "http://env-inf"
    assert connection.tools_url() == "http://env-tools"
    assert connection.inference_token() == "env-tok"
    assert connection.tools_token() == "env-tool"

    connection.set_connection("http://o-inf", "http://o-tools", "o-tok")
    assert connection.inference_url() == "http://o-inf"
    assert connection.tools_url() == "http://o-tools"
    assert connection.inference_token() == "o-tok"
    assert connection.tools_token() == "o-tok"

    connection.set_connection("", "", "")
    assert connection.inference_url() == "http://env-inf"
    assert connection.tools_url() == "http://env-tools"
    assert connection.inference_token() == "env-tok"
    assert connection.tools_token() == "env-tool"


def test_tools_url_strips_trailing_slash() -> None:
    connection.set_connection(None, "http://pod:8700/", None)
    assert connection.tools_url() == "http://pod:8700"


def test_token_set() -> None:
    assert connection.token_set() is False
    connection.set_connection(None, None, "x")
    assert connection.token_set() is True


def test_token_set_from_env_of_either_server(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "audyssey_tool_token", SecretStr("t"))
    assert connection.token_set() is True


def test_base_client_uses_override_url_and_bearer() -> None:
    connection.set_connection("http://gpu:8080/", "", "o-tok")

    async def run():
        async with BaseClient() as client:
            return client.base_url, client._client.headers.get("authorization")

    assert asyncio.run(run()) == ("http://gpu:8080", "Bearer o-tok")


def test_audyssey_post_uses_override_url_and_bearer(tmp_path) -> None:
    connection.set_connection(None, "http://tools:8700", "o-tok")
    response = MagicMock(status_code=200, content=b"wav")
    with patch.object(audyssey.httpx, "post", return_value=response) as post:
        audyssey._post("separate", {}, {}, tmp_path / "out.wav")
    assert post.call_args.args[0] == "http://tools:8700/separate"
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer o-tok"}


def _client(status: str = "idle") -> TestClient:
    store = MagicMock(spec=VideoAssetStore)
    store.snapshot.return_value = MagicMock(status=status)
    app = FastAPI()
    app.include_router(create_router(store))
    return TestClient(app)


def _post(client: TestClient, **body):
    payload = {"server_url": "", "tools_url": "", "token": None} | body
    return client.post("/api/connection", json=payload)


def test_get_connection_shape() -> None:
    r = _client().get("/api/connection")
    assert r.status_code == 200
    assert r.json() == {
        "server_url": "",
        "tools_url": "http://127.0.0.1:8700",
        "token_set": False,
    }


def test_post_sets_strips_and_hides_token() -> None:
    client = _client()
    r = _post(
        client, server_url=" https://a-8080.proxy ", tools_url="http://b", token=SECRET
    )
    assert r.status_code == 200
    assert r.json() == {
        "server_url": "https://a-8080.proxy",
        "tools_url": "http://b",
        "token_set": True,
    }
    assert SECRET not in r.text
    assert SECRET not in client.get("/api/connection").text
    assert connection.inference_token() == SECRET


def test_token_null_keeps_and_empty_clears() -> None:
    client = _client()
    _post(client, token=SECRET)
    assert _post(client, server_url="http://x", token=None).json()["token_set"] is True
    assert connection.tools_token() == SECRET
    assert _post(client, token="").json()["token_set"] is False
    assert connection.tools_token() is None


def test_empty_url_clears_override() -> None:
    client = _client()
    _post(client, server_url="http://x", tools_url="http://y")
    r = _post(client, server_url="", tools_url="")
    assert r.json()["server_url"] == ""
    assert r.json()["tools_url"] == "http://127.0.0.1:8700"


@pytest.mark.parametrize("field", ["server_url", "tools_url"])
def test_invalid_url_is_422(field) -> None:
    assert _post(_client(), **{field: "gpu:8080"}).status_code == 422


def test_conflict_while_running() -> None:
    r = _post(_client("running"), server_url="http://x")
    assert r.status_code == 409
    assert r.json() == {"detail": "Pipeline is running"}
    assert connection.inference_url() is None
