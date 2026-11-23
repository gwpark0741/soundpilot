from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from v2a_inspect_server import app as app_module
from v2a_inspect_server.app import app


class ApiTokenTest(unittest.TestCase):
    def test_token_set_protects_everything_but_healthz(self) -> None:
        with patch.object(app_module.settings, "api_token", "t"):
            client = TestClient(app)
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.post("/videos/upload").status_code, 401)
            bad = client.post("/videos/upload", headers={"Authorization": "Bearer x"})
            self.assertEqual(bad.status_code, 401)
            ok = client.post("/videos/upload", headers={"Authorization": "Bearer t"})
            self.assertNotEqual(ok.status_code, 401)

    def test_blank_token_means_no_auth(self) -> None:
        with patch.object(app_module.settings, "api_token", ""):
            response = TestClient(app).post("/videos/upload")
            self.assertNotEqual(response.status_code, 401)
