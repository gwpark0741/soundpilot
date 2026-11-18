"""Demo preflight and warmup. Exit 0 only if every check passes.

uv run python scripts/demo_preflight.py [--ui URL] [--gpu N] [--warmup VIDEO ...]
Keys come from the environment (GEMINI_API_KEY, ELEVENLABS_API_KEY); UI password
from V2A_INSPECT_UI_PASSWORD. Nothing secret is printed.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

TOOLS_IMAGE = "audyssey-tools:demo-1117"
TOOLS_IMAGE_ID = "sha256:cc603e8ffc46c26da15aac051996271db8c06bdffe1134c1bdf7b6291d08694a"
HF_ROOT = Path("/home/common/huggingface")
PINS = {
    "facebook/sam3.1": "daa63191845a41281374e725f4c9e51c7a824460",
    "sogang-audyssey/internal-model": "2b5bb25e4212cd1bb9eef46ff2c344cdfa8ef1b8",
    "hexgrad/Kokoro-82M": "f3ff3571791e39611d31c381e3a41a3af07b4987",
}
COMPOSE = ["docker", "compose", "-f", "compose.demo.yaml"]
GEMINI_MODEL = os.environ.get("V2A_INSPECT_LLM_LARGE_MODEL", "gemini-4-hydrogen")


def check_snapshots(hf_root: Path, pins: dict[str, str]) -> list[str]:
    errors = []
    for repo, rev in pins.items():
        snap = hf_root / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots" / rev
        if not snap.is_dir():
            errors.append(f"{repo}@{rev[:8]} snapshot missing")
    return errors


def parse_gpu_used_mib(nvidia_smi_csv: str, gpu: int) -> int:
    for line in nvidia_smi_csv.splitlines():
        idx, _, used = line.partition(",")
        if idx.strip() == str(gpu):
            return int(used.split()[0])
    raise ValueError(f"gpu {gpu} not in nvidia-smi output")


def run(cmd: list[str], timeout: int = 30) -> str:
    """stdout on success; raises RuntimeError with a short reason otherwise."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{cmd[0]}: {type(exc).__name__}") from exc
    if p.returncode:
        raise RuntimeError((p.stderr.strip().splitlines() or ["exit " + str(p.returncode)])[-1][:150])
    return p.stdout


def gpu_used(gpu: int) -> int:
    return parse_gpu_used_mib(
        run(["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader"]), gpu
    )


# Each check returns None on success or a reason string.
def check_image() -> str | None:
    got = run(["docker", "image", "inspect", TOOLS_IMAGE, "--format", "{{.Id}}"]).strip()
    return None if got == TOOLS_IMAGE_ID else f"{TOOLS_IMAGE} id mismatch"


def check_weights() -> str | None:
    errs = check_snapshots(HF_ROOT, PINS)
    return "; ".join(errs) or None


def check_containers() -> str | None:
    running = set(run(COMPOSE + ["ps", "--status", "running", "--services"]).split())
    missing = {"ui", "inference", "tools"} - running
    return f"not running: {', '.join(sorted(missing))}" if missing else None


def _exec_get(svc: str, port: int, path: str) -> None:
    code = f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}{path}', timeout=5).read()"
    run(COMPOSE + ["exec", "-T", svc, "python", "-c", code])


def check_inference() -> str | None:
    _exec_get("inference", 8080, "/healthz")


def check_tools() -> str | None:
    try:
        _exec_get("tools", 8700, "/health")
    except RuntimeError:
        return "tools still loading (cold load ~160 s) or down"


def check_ui(ui: str) -> str | None:
    r = httpx.get(f"{ui}/healthz", timeout=5)
    return None if r.status_code == 200 else f"HTTP {r.status_code}"


def _key_check(url: str, headers: dict[str, str] | None) -> str | None:
    if headers is None:
        return "key not set in environment"
    r = httpx.get(url, headers=headers, timeout=10)
    return None if r.status_code == 200 else f"HTTP {r.status_code}"


def check_gemini() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"
    return _key_check(url, {"x-goog-api-key": key} if key else None)


def check_elevenlabs() -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    return _key_check("https://api.elevenlabs.io/v1/user", {"xi-api-key": key} if key else None)


def _our_pids() -> set[str]:
    pids: set[str] = set()
    try:
        for cid in run(COMPOSE + ["ps", "-q"]).split():
            pids.update(run(["docker", "top", cid, "-eo", "pid"]).split()[1:])
    except RuntimeError:
        pass
    return pids


def check_gpu(gpu: int) -> str | None:
    used = gpu_used(gpu)
    print(f"      gpu {gpu}: {used} MiB used")
    bus = {}
    for line in run(["nvidia-smi", "--query-gpu=index,pci.bus_id", "--format=csv,noheader"]).splitlines():
        i, _, b = line.partition(",")
        bus[b.strip()] = i.strip()
    ours, me = _our_pids(), str(os.getuid())
    others = []
    out = run(["nvidia-smi", "--query-compute-apps=gpu_bus_id,pid", "--format=csv,noheader"])
    for line in out.splitlines():
        b, _, pid = line.partition(",")
        pid = pid.strip()
        if bus.get(b.strip()) != str(gpu) or pid in ours:
            continue
        try:
            uid = run(["ps", "-o", "uid=", "-p", pid]).strip()
        except RuntimeError:
            uid = "?"
        if uid != me:
            others.append(pid)
    return f"{len(others)} other process(es) on gpu {gpu}" if others else None


def report(name: str, fn, *args) -> bool:
    try:
        reason = fn(*args)
    except Exception as exc:  # noqa: BLE001 - a check must never crash the run
        reason = f"{type(exc).__name__}: {str(exc)[:150]}"
    print(f"{'OK  ' if reason is None else 'FAIL'} {name}" + (f": {reason}" if reason else ""))
    return reason is None


def _wait_complete(c: httpx.Client, ui: str, gpu: int | None, timeout: float = 1800) -> tuple[float, int]:
    start, peak = time.monotonic(), 0
    while time.monotonic() - start < timeout:
        s = c.get(f"{ui}/api/asset-summary").json()
        if gpu is not None:
            try:
                peak = max(peak, gpu_used(gpu))
            except (RuntimeError, ValueError):
                gpu = None
        if s["status"] == "failed":
            raise RuntimeError(f"pipeline failed: {s.get('error')}")
        if s["status"] == "complete":
            return time.monotonic() - start, peak
        time.sleep(2)
    raise RuntimeError("timed out")


def warmup(ui: str, videos: list[str], gpu: int | None) -> bool:
    ok = True
    with httpx.Client(timeout=60) as c:
        pw = os.environ.get("V2A_INSPECT_UI_PASSWORD")
        if pw:  # same call the login form makes; the cookie lands in the client jar
            c.post(f"{ui}/login", data={"password": pw})
        for video in videos:
            try:
                times: dict[str, float] = {}
                peak = 0
                with open(video, "rb") as f:
                    r = c.post(f"{ui}/api/runs", files={"video": (Path(video).name, f)}, timeout=600)
                r.raise_for_status()
                times["analysis"], p = _wait_complete(c, ui, gpu)
                peak = max(peak, p)
                for mode in ("generate", "separate"):
                    m = c.post(f"{ui}/api/audio/mode", data={"mode": mode})
                    if m.status_code == 422:  # separate needs original audio
                        print(f"      {video}: {mode} skipped (no audio)")
                        continue
                    m.raise_for_status()
                    c.post(f"{ui}/api/audio/generate").raise_for_status()
                    times[mode], p = _wait_complete(c, ui, gpu)
                    peak = max(peak, p)
                stages = ", ".join(f"{k} {v:.0f}s" for k, v in times.items())
                print(f"OK   warmup {video}: {stages}; peak gpu {peak} MiB")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"FAIL warmup {video}: {type(exc).__name__}: {str(exc)[:150]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default="http://127.0.0.1:8501")
    ap.add_argument("--gpu", type=int)
    ap.add_argument("--warmup", nargs="+", metavar="VIDEO")
    a = ap.parse_args()
    results = [
        report("tools image", check_image),
        report("weights", check_weights),
        report("containers", check_containers),
        report("inference /healthz", check_inference),
        report("tools /health", check_tools),
        report("ui /healthz", check_ui, a.ui),
        report("gemini key", check_gemini),
        report("elevenlabs key", check_elevenlabs),
    ]
    if a.gpu is not None:
        results.append(report("gpu free of other users", check_gpu, a.gpu))
    if a.warmup:
        results.append(warmup(a.ui, a.warmup, a.gpu))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
