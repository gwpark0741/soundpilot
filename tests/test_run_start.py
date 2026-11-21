from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v2a_inspect.models import VideoAsset
from v2a_inspect.ui.routes import create_router
from v2a_inspect.ui.store import VideoAssetStore


class StartRunTest(unittest.TestCase):
    def test_new_run_clears_previous_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VideoAssetStore()
            asyncio.run(
                store.set_complete_asset(
                    VideoAsset(source_path=Path(temp_dir) / "old.mp4", frame_count=30)
                )
            )
            before = asyncio.run(store.snapshot())
            app = FastAPI()
            app.include_router(create_router(store))
            with patch("v2a_inspect.ui.routes.run_uploaded_video_pipeline"):
                response = TestClient(app).post(
                    "/api/runs",
                    files={"video": ("new.mp4", b"x", "video/mp4")},
                    data={"work_dir": temp_dir},
                )
            after = asyncio.run(store.snapshot())

            self.assertEqual(response.status_code, 200)
            self.assertIsNone(after.asset)
            self.assertEqual(after.status, "running")
            self.assertEqual(after.current_stage, "queued")
            self.assertGreater(after.asset_version, before.asset_version)
