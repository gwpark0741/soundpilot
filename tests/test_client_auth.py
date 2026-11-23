import asyncio
from unittest.mock import patch

from v2a_inspect.client.config.settings import settings
from v2a_inspect.client.endpoints.base import BaseClient


def _auth_header(token: str):
    async def run():
        with patch.object(settings, "server_token", token):
            async with BaseClient("http://x") as client:
                return client._client.headers.get("authorization")

    return asyncio.run(run())


def test_token_sent_as_bearer() -> None:
    assert _auth_header("t") == "Bearer t"


def test_blank_token_sends_no_header() -> None:
    assert _auth_header("") is None
