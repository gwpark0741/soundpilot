import io
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from v2a_inspect.models import VideoAsset
from v2a_inspect.ui.bundle import read_bundle, write_bundle


def _asset(root: Path) -> VideoAsset:
    (root / "uploads").mkdir(parents=True)
    (root / "stems").mkdir()
    files = {
        "src": root / "uploads" / "v.mp4",
        "stem": root / "stems" / "track.wav",
        "sep": root / "stems" / "sep.wav",
        "res": root / "stems" / "residual.wav",
    }
    for f in files.values():
        f.write_bytes(b"x")
    return VideoAsset.model_validate(
        {
            "source_path": str(files["src"]),
            "frame_count": 1,
            "separated_stem_paths": {str(uuid4()): str(files["sep"])},
            "residual_path": str(files["res"]),
        }
    )


def test_roundtrip_rewrites_paths(tmp_path):
    old = tmp_path / "a" / "v2a-inspect-ui"
    new = tmp_path / "b" / "v2a-inspect-ui"
    asset = _asset(old)
    buf = io.BytesIO()
    write_bundle(asset, old, buf)
    buf.seek(0)
    out = read_bundle(buf, new)
    dumped = out.model_dump_json(exclude_computed_fields=True)
    assert str(old) not in dumped
    for p in [out.source_path, out.residual_path, *out.separated_stem_paths.values()]:
        assert p.is_relative_to(new) and p.exists()


@pytest.mark.parametrize("name", ["../x", "/abs"])
def test_rejects_unsafe_members(tmp_path, name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("asset.json", "{}")
        zf.writestr(name, "x")
    buf.seek(0)
    with pytest.raises(ValueError):
        read_bundle(buf, tmp_path / "v2a-inspect-ui")


def test_missing_asset_json(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("files/x", "x")
    buf.seek(0)
    with pytest.raises(ValueError):
        read_bundle(buf, tmp_path / "v2a-inspect-ui")
