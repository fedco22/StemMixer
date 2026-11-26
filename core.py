"""Fungsi inti Stem Mixer: baca/tulis audio, pemisahan Demucs, pitch & tempo.
Dipakai oleh app.py (desktop) dan server.py (web lokal)."""

import json
import re
from pathlib import Path

import numpy as np
SR = 44100
APP_DIR = Path.home() / "StemMixer"

STEM_LABELS = {
    "vocals": "Vokal",
    "drums": "Drum",
    "bass": "Bass",
    "guitar": "Gitar",
    "piano": "Piano",
    "other": "Lainnya",
}
STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other"]

MODELS = {
    "htdemucs": "Standar – 4 stem (vokal, drum, bass, lainnya)",
    "htdemucs_ft": "Kualitas tinggi – 4 stem (±4x lebih lambat)",
    "htdemucs_6s": "6 stem (+ gitar & piano)",
}

# ----------------------------------------------------------------------------
# Audio helpers
# ----------------------------------------------------------------------------
def load_audio(path):
    """Baca file audio -> float32 (2, n) pada 44.1 kHz."""
    from pedalboard.io import AudioFile

    with AudioFile(str(path)).resampled_to(SR) as f:
        audio = f.read(f.frames)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[None, :]
    if audio.shape[0] == 1:
        audio = np.vstack([audio, audio])
    elif audio.shape[0] > 2:
        audio = audio[:2]
    return np.ascontiguousarray(audio)


def save_wav(path, audio):
    from pedalboard.io import AudioFile

    audio = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    with AudioFile(str(path), "w", SR, audio.shape[0], bit_depth=16) as f:
        f.write(audio)


def separate(path, model_name, status_cb, progress_cb):
    """Pisahkan lagu memakai Demucs. Return dict nama_stem -> array (2, n)."""
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    import demucs.apply as dapply

    status_cb("Memuat model AI… (pertama kali akan mengunduh model, perlu internet)")
    model = get_model(model_name)
    model.eval()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    status_cb("Membaca file audio…")
    wav = torch.from_numpy(load_audio(path))

    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav_n = (wav - mean) / std

    # Kaitkan progress bar Demucs (tqdm) ke progress bar aplikasi
    n_models = len(getattr(model, "models", [model]))
    state = {"pass": 0}

    class _FakeTqdm:
        @staticmethod
        def tqdm(iterable, **kwargs):
            items = list(iterable)
            total = max(len(items), 1)
            for i, item in enumerate(items):
                yield item
                progress_cb(min((state["pass"] + (i + 1) / total) / n_models, 1.0))
            state["pass"] += 1

    status_cb(f"Memisahkan suara dengan {'GPU' if device == 'cuda' else 'CPU'}… "
              f"(bisa beberapa menit)")
    original_tqdm = getattr(dapply, "tqdm", None)
    hook = original_tqdm is not None and hasattr(original_tqdm, "tqdm")
    if hook:
        dapply.tqdm = _FakeTqdm
    try:
        with torch.no_grad():
            sources = apply_model(model, wav_n[None], device=device, shifts=1,
                                  split=True, overlap=0.25, progress=hook)[0]
    finally:
        if hook:
            dapply.tqdm = original_tqdm

    sources = sources * std + mean
    return {name: np.ascontiguousarray(sources[i].cpu().numpy().astype(np.float32))
            for i, name in enumerate(model.sources)}


def process_stem(audio, semitones, speed):
    """Ubah pitch (semitone) dan tempo (speed: 1.0 = asli, 1.2 = 20% lebih cepat)."""
    if semitones == 0 and abs(speed - 1.0) < 1e-6:
        return audio
    from pedalboard import time_stretch

    out = time_stretch(np.ascontiguousarray(audio, dtype=np.float32), SR,
                       stretch_factor=float(speed),
                       pitch_shift_in_semitones=float(semitones))
    return np.ascontiguousarray(out, dtype=np.float32)


def sort_stems(names):
    return sorted(names, key=lambda n: STEM_ORDER.index(n) if n in STEM_ORDER else 99)


def safe_name(text):
    return re.sub(r"[^\w\-. ]+", "_", text).strip() or "lagu"



# ----------------------------------------------------------------------------
# Pustaka lagu (folder cache di ~/StemMixer)
# ----------------------------------------------------------------------------
def song_dir_for(source_path, model):
    return APP_DIR / f"{safe_name(Path(source_path).stem)} [{model}]"


def separate_to_folder(source_path, model, status_cb, progress_cb):
    """Pisahkan lagu lalu simpan stem ke folder cache. Return folder."""
    folder = song_dir_for(source_path, model)
    stems = separate(source_path, model, status_cb, progress_cb)
    status_cb("Menyimpan stem…")
    folder.mkdir(parents=True, exist_ok=True)
    for name, audio in stems.items():
        save_wav(folder / f"{name}.wav", audio)
    (folder / "info.json").write_text(json.dumps(
        {"source": str(source_path), "model": model, "stems": list(stems)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def song_stems(folder):
    return sort_stems(f.stem for f in Path(folder).glob("*.wav"))


def list_songs():
    songs = []
    if not APP_DIR.exists():
        return songs
    for d in sorted(APP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        stems = song_stems(d)
        if not stems:
            continue
        m = re.match(r"^(.*?)\s*\[(.+)\]$", d.name)
        songs.append({"id": d.name,
                      "title": m.group(1) if m else d.name,
                      "model": m.group(2) if m else "",
                      "stems": stems})
    return songs


def variant_name(semitones, speed):
    if semitones == 0 and abs(speed - 1.0) < 1e-6:
        return "orig"
    return f"pt_{semitones:+d}_{round(speed * 100)}"


def make_variant(folder, semitones, speed, status_cb, progress_cb):
    """Buat versi pitch/tempo dari semua stem (disimpan & di-cache)."""
    folder = Path(folder)
    name = variant_name(semitones, speed)
    if name == "orig":
        return folder
    out = folder / "_variants" / name
    stems = song_stems(folder)
    if out.exists() and all((out / f"{s}.wav").exists() for s in stems):
        return out
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(stems):
        status_cb(f"Mengubah pitch/tempo: {STEM_LABELS.get(s, s)}…")
        audio = process_stem(load_audio(folder / f"{s}.wav"), semitones, speed)
        save_wav(out / f"{s}.wav", audio)
        progress_cb((i + 1) / len(stems))
    return out
