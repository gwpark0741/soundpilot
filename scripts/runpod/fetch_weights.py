"""Download every Hugging Face file the one-pod demo needs into $HF_HOME (pinned revisions).

  fetch_weights.py           download what is missing (needs HF_TOKEN)
  fetch_weights.py --check   offline: list present/missing, exit 1 if anything is missing

Pins match the weights the demo was tested with (/home/common/huggingface on sv3-a100).
Files were chosen by tracing what the servers open with HF_HUB_OFFLINE=1; see docs/runpod.md.
Run with the inference server's Python (/app/server/.venv/bin/python).
"""

import os
import sys
from pathlib import Path

# (repo, revision, files, ref written to refs/<ref>, approx GB). The Judge needs two revisions:
# weights on the `sam_audio` branch, config and tokenizer on `main` (see audyssey docs/running.md).
WEIGHTS = [
    # inference server
    ("facebook/sam3.1", "daa63191845a41281374e725f4c9e51c7a824460", ["config.json", "sam3.1_multiplex.pt"], "main", 3.5),
    ("hexgrad/Kokoro-82M", "f3ff3571791e39611d31c381e3a41a3af07b4987", ["config.json", "kokoro-v1_0.pth", "voices/af_heart.pt"], "main", 0.33),
    # tools: separation (SAM Audio, text encoder, reranking with CLAP + Judge)
    ("facebook/sam-audio-large-tv", "b9fc94687ec044c570cbe30b8c28100cd056f1cb", ["config.json", "checkpoint.pt"], "main", 14.9),
    ("t5-base", "a9723ea7f1b39c1eae772870f3b547bf6ef7e6c1", ["config.json", "model.safetensors", "spiece.model", "tokenizer.json"], "main", 0.9),
    ("facebook/sam-audio-judge", "cd52eb977697cc0b25d4ecd309a88d4af46223ee", ["checkpoint.pt", "config.json"], "sam_audio", 6.1),
    ("facebook/sam-audio-judge", "d461b0b9bd4d139966a1a101fa147427eb46638d", ["config.json", "preprocessor_config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"], "main", 0.002),
    ("lukewys/laion_clap", "b3708341862f581175dba5c356a4ebf74a9b6651", ["630k-best.pt"], "main", 1.9),
    ("roberta-base", "e2da8e2f811d1448a5b465c236feacd80ffbac7b", ["config.json", "merges.txt", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "vocab.json"], "main", 0.5),
    ("bert-base-uncased", "86b5e0934494bd15c9632b12f734a8a67f723594", ["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt"], "main", 0.001),
    ("facebook/bart-base", "aadd2ab0ae0c8268c7c9693540e9904811f36177", ["config.json", "merges.txt", "tokenizer.json", "vocab.json"], "main", 0.003),
    # tools: generation (SelVA base + internal model, CLIP, T5, vocoder)
    ("jnwnlee/SelVA", "857f32db7f80f151e124f5f4cfb816397ddfac85", ["weights/video_enc_sup_5.pth", "ext_weights/v1-44.pth", "ext_weights/synchformer_state_dict.pth"], "main", 2.7),
    ("sogang-audyssey/internal-model", "2b5bb25e4212cd1bb9eef46ff2c344cdfa8ef1b8", ["generator/S_O_s43/model_ema_step1000.pth", "encoder/S_O/best.pt"], "main", 4.2),
    ("apple/DFN5B-CLIP-ViT-H-14-384", "01b771ed0d1395ca5ffdd279897d665ebe00dfd2", ["open_clip_config.json", "open_clip_pytorch_model.bin"], "main", 3.95),
    ("google/flan-t5-base", "7bcac572ce56db69c1ea7c8af255c5d7c9672fc2", ["config.json", "model.safetensors", "spiece.model", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"], "main", 1.0),
    ("nvidia/bigvgan_v2_44khz_128band_512x", "95a9d1dcb12906c03edd938d77b9333d6ded7dfb", ["bigvgan_generator.pt", "config.json"], "main", 0.49),
]


def hub_dir() -> Path:
    return Path(os.environ.get("HF_HOME", "/workspace/hf")) / "hub"


def snapshot(repo: str, rev: str) -> Path:
    return hub_dir() / f"models--{repo.replace('/', '--')}" / "snapshots" / rev


def missing_files(repo: str, rev: str, files: list[str]) -> list[str]:
    return [f for f in files if not (snapshot(repo, rev) / f).is_file()]


def write_ref(repo: str, rev: str, ref: str) -> None:
    # offline lookups of "main" (or the sam_audio branch) read refs/<ref>
    path = snapshot(repo, rev).parents[1] / "refs" / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(rev)


def main() -> int:
    check = "--check" in sys.argv[1:]
    todo = [(r, v, m, ref, gb) for r, v, f, ref, gb in WEIGHTS if (m := missing_files(r, v, f))]
    for repo, rev, files, ref, gb in WEIGHTS:
        status = "missing" if any(t[:2] == (repo, rev) for t in todo) else "ok"
        print(f"{status:8s}{repo}@{rev[:8]} ({ref}, ~{gb} GB)")
    if check or not todo:
        return 1 if todo else 0

    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import HfHubHTTPError

    token = os.environ.get("HF_TOKEN")
    denied = []
    for repo, rev, missing, ref, _ in todo:
        try:
            for name in missing:
                print(f"download {repo}/{name}", flush=True)
                hf_hub_download(repo, name, revision=rev, cache_dir=hub_dir(), token=token)
            write_ref(repo, rev, ref)
        except HfHubHTTPError as exc:  # gated without approval, private without org membership, bad token
            denied.append(repo)
            print(f"ERROR   {repo}: {type(exc).__name__}: no access with this HF_TOKEN", file=sys.stderr)
    if denied:
        print("no access to: " + ", ".join(sorted(set(denied))), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
