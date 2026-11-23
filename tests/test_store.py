from __future__ import annotations

import asyncio
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from v2a_inspect.models import VideoAsset
from v2a_inspect.ui.store import VideoAssetStore


class StoreSaveTest(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_is_not_blocked_while_the_project_saves(self) -> None:
        saving = threading.Event()
        release = threading.Event()

        def slow_save(_asset: VideoAsset) -> None:
            saving.set()
            release.wait(5)

        store = VideoAssetStore()
        asset = VideoAsset(source_path=Path("clip.mp4"), frame_count=30)
        with patch("v2a_inspect.ui.store.save_project_logged", slow_save):
            task = asyncio.create_task(store.set_complete_asset(asset))
            await asyncio.to_thread(saving.wait, 5)
            try:
                snapshot = await asyncio.wait_for(store.snapshot(), 1)
                self.assertNotEqual(snapshot.status, "complete")
            finally:
                release.set()
            await task

        snapshot = await store.snapshot()
        self.assertEqual(snapshot.status, "complete")
        self.assertIs(snapshot.asset, asset)
        self.assertEqual(snapshot.asset_version, 1)
