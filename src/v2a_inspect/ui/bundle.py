from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from v2a_inspect.models import VideoAsset
from pydantic import ValidationError


def write_bundle(asset: VideoAsset, root: Path, out: BinaryIO) -> None:
    root = root.resolve()
    # Asset paths are absolute or relative to the UI's cwd; record both spellings.
    roots = [str(root), os.path.relpath(root)]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "asset.json",
            asset.model_dump_json(exclude_computed_fields=True),
        )
        zf.writestr("bundle.json", json.dumps({"bundle_roots": roots}))
        # ponytail: zips the whole work root (may include other uploads); filter to referenced paths if bundles get large.
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, f"files/{path.relative_to(root).as_posix()}")


def read_bundle(data: BinaryIO, dest_root: Path) -> VideoAsset:
    try:
        zf = zipfile.ZipFile(data)
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a zip file") from exc
    with zf:
        names = zf.namelist()
        for name in names:
            parts = PurePosixPath(name).parts
            if name.startswith(("/", "\\")) or ".." in parts or "\\" in name:
                raise ValueError(f"Unsafe bundle member: {name!r}")
        if "asset.json" not in names or "bundle.json" not in names:
            raise ValueError("Bundle is missing asset.json or bundle.json")
        old_roots = json.loads(zf.read("bundle.json"))["bundle_roots"]
        dest_root = dest_root.resolve()
        for name in names:
            if name.startswith("files/") and not name.endswith("/"):
                target = dest_root / name.removeprefix("files/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
        text = zf.read("asset.json").decode()
        new = json.dumps(str(dest_root))[1:-1]
        for old in sorted(old_roots, key=len, reverse=True):
            text = text.replace(f'"{json.dumps(old)[1:-1]}/', f'"{new}/')
    try:
        return VideoAsset.model_validate_json(text)
    except ValidationError as exc:
        raise ValueError(f"Invalid VideoAsset JSON: {exc}") from exc
