from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from v2a_inspect_server import app as app_module
from v2a_inspect_server.app import app


class ServerAppTest(unittest.TestCase):
    def test_unused_vision_endpoints_are_not_registered(self) -> None:
        paths = {route.path for route in app.routes}

        self.assertNotIn("/infer/dinov2/embed-images", paths)
        self.assertNotIn("/infer/score", paths)

    def test_hunyuan_disabled_skips_client_and_returns_503(self) -> None:
        with (
            patch.object(app_module.settings, "hunyuan_enabled", False),
            patch.object(app_module, "Sam3InferenceClient"),
            patch.object(app_module, "KokoroInferenceClient"),
            patch.object(app_module, "HunyuanInferenceClient") as hunyuan_cls,
            TestClient(app) as client,
        ):
            response = client.post(
                "/infer/hunyuan/generate-v2a",
                json={
                    "video_id": "v",
                    "start_frame_index": 0,
                    "end_frame_index": 1,
                    "prompt": "p",
                },
            )

        hunyuan_cls.assert_not_called()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"], "Hunyuan is disabled on this server"
        )


if __name__ == "__main__":
    unittest.main()
