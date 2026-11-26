"""Unduh model AI htdemucs saat build, lalu salin cache-nya ke models/ untuk dibundel.

Demucs lama menyimpan model di cache torch hub; Demucs >= 4.1 memakai cache
Hugging Face. Keduanya diarahkan ke folder sendiri lalu disalin utuh.
"""
import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
cache = root / "model_cache"
os.environ["TORCH_HOME"] = str(cache / "torch")
os.environ["HF_HOME"] = str(cache / "hf")
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from demucs.pretrained import get_model  # noqa: E402

get_model("htdemucs")

dst = root / "models"
shutil.rmtree(dst, ignore_errors=True)
total = 0
for sub in ("torch", "hf"):
    src = cache / sub
    if src.is_dir():
        shutil.copytree(src, dst / sub, symlinks=False)
for f in dst.rglob("*"):
    if f.is_file():
        total += f.stat().st_size
        if f.stat().st_size > 1_000_000:
            print("bundled:", f.relative_to(dst), f.stat().st_size // 1_000_000, "MB")
print(f"total {total // 1_000_000} MB")
if total < 10_000_000:
    raise SystemExit("Model tidak ditemukan di cache!")
