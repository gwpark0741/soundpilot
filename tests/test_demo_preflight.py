import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "demo_preflight", Path(__file__).parent.parent / "scripts" / "demo_preflight.py"
)
dp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp)


def test_check_snapshots(tmp_path):
    (tmp_path / "hub/models--a--b/snapshots/abc").mkdir(parents=True)
    assert dp.check_snapshots(tmp_path, {"a/b": "abc"}) == []
    errs = dp.check_snapshots(tmp_path, {"a/b": "abc", "c/d": "x"})
    assert len(errs) == 1 and "c/d" in errs[0]
    assert len(dp.check_snapshots(tmp_path, {"a/b": "zzz"})) == 1


def test_parse_gpu_used_mib():
    assert dp.parse_gpu_used_mib("0, 1 MiB\n7, 36712 MiB\n", 7) == 36712
    with pytest.raises(ValueError):
        dp.parse_gpu_used_mib("0, 1 MiB\n", 3)
