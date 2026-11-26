"""Unduh model AI htdemucs saat build, lalu taruh di folder models/ untuk dibundel."""
import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
cache = root / "torch_cache"
os.environ["TORCH_HOME"] = str(cache)

from demucs.pretrained import get_model  # noqa: E402

get_model("htdemucs")
dst = root / "models"
dst.mkdir(exist_ok=True)
for f in (cache / "hub" / "checkpoints").iterdir():
    shutil.copy2(f, dst / f.name)
    print("bundled model:", f.name, f.stat().st_size // 1_000_000, "MB")
