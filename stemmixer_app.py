"""
Stem Mixer – aplikasi Windows
=============================
Membuka antarmuka Stem Mixer di jendela aplikasi sendiri (WebView2 bawaan
Windows), dengan server lokal di belakangnya. Tidak perlu Python atau browser.

  StemMixer.exe                    -> buka aplikasi
  StemMixer.exe --selftest log.txt -> uji mesin AI tanpa jendela (dipakai saat build)
"""

import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path.home() / "StemMixer"

# Model AI disimpan di folder data aplikasi (bisa ditulis), bukan di folder instalasi.
MODEL_CACHE = DATA_DIR / "_models"
os.environ.setdefault("TORCH_HOME", str(MODEL_CACHE / "torch"))
os.environ.setdefault("HF_HOME", str(MODEL_CACHE / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def fix_streams():
    # Mode tanpa konsol: stdout/stderr = None -> progress unduhan bisa crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def seed_models():
    """Salin model AI bawaan installer ke cache, agar tidak perlu unduh."""
    src = HERE / "models"
    if not src.is_dir():
        return
    for sub, env in (("torch", "TORCH_HOME"), ("hf", "HF_HOME")):
        base = src / sub
        if not base.is_dir():
            continue
        dst_root = Path(os.environ[env])
        for f in base.rglob("*"):
            if f.is_file():
                d = dst_root / f.relative_to(base)
                if not d.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, d)


# ----------------------------------------------------------------------------
# API yang dipanggil dari halaman (window.pywebview.api.*)
# ----------------------------------------------------------------------------
class Api:
    def __init__(self):
        self.window = None

    def _ask_save(self, filename):
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "SAVE", None)
        if kind is None:
            kind = webview.SAVE_DIALOG
        res = self.window.create_file_dialog(kind, save_filename=filename,
                                             file_types=("File WAV (*.wav)",))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        if not path.lower().endswith(".wav"):
            path += ".wav"
        return path

    def save_stem(self, song, variant, stem, filename):
        try:
            import server
            src = server.stem_file(song, variant, stem)
            path = self._ask_save(filename)
            if not path:
                return None
            shutil.copy2(src, path)
            return {"path": path}
        except Exception as e:
            return {"error": str(e)}

    def save_mix(self, opts):
        try:
            import numpy as np
            import core
            import server
            path = self._ask_save(opts["filename"])
            if not path:
                return None
            master = float(opts.get("master", 1.0))
            mix, length = None, None
            for stem, gain in opts["gains"].items():
                a = core.load_audio(server.stem_file(opts["song"], opts["variant"], stem))
                length = a.shape[1] if length is None else min(length, a.shape[1])
                if mix is None:
                    mix = np.zeros_like(a)
                n = min(mix.shape[1], a.shape[1])
                if gain > 0:
                    mix[:, :n] += a[:, :n] * (float(gain) * master)
            mix = mix[:, :length]
            clipped = bool(np.max(np.abs(mix)) > 1.0) if mix.size else False
            core.save_wav(path, mix)
            return {"path": path, "clipped": clipped}
        except Exception as e:
            return {"error": str(e)}

    def open_folder(self, path):
        try:
            p = Path(path)
            os.startfile(str(p.parent if p.is_file() else p))  # Windows
        except Exception:
            pass


# ----------------------------------------------------------------------------
def run_app():
    fix_streams()
    seed_models()
    import server

    httpd = server.create_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    try:
        import webview
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass
        api = Api()
        win = webview.create_window("Stem Mixer", url, js_api=api, width=1240, height=840,
                                    min_size=(980, 660), background_color="#0f1115")
        api.window = win
        webview.start()
    except Exception:
        # Cadangan: buka di browser bila komponen jendela (WebView2) tidak ada.
        (DATA_DIR / "_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        import webbrowser
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    httpd.shutdown()


def selftest(log_path):
    """Uji seluruh mesin: baca/tulis audio, AI Demucs, pitch/tempo, server."""
    fix_streams()
    log = open(log_path, "w", encoding="utf-8")

    def say(msg):
        log.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        log.flush()

    try:
        seed_models()
        import numpy as np
        import core
        import server
        tmp = DATA_DIR / "_selftest"
        tmp.mkdir(parents=True, exist_ok=True)

        sr = core.SR
        t = np.arange(sr * 8) / sr
        x = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.05 * np.random.default_rng(0).standard_normal(len(t))
        audio = np.vstack([x, x]).astype(np.float32)
        core.save_wav(tmp / "test.wav", audio)
        back = core.load_audio(tmp / "test.wav")
        assert back.shape == audio.shape, f"wav shape {back.shape}"
        say("baca/tulis WAV: OK")

        try:
            from pedalboard.io import AudioFile
            with AudioFile(str(tmp / "test.mp3"), "w", sr, 2) as f:
                f.write(audio)
            mp3 = core.load_audio(tmp / "test.mp3")
            say(f"baca MP3: OK {mp3.shape}")
        except Exception as e:
            say(f"PERINGATAN mp3: {e}")

        say("pemisahan AI (htdemucs, CPU)…")
        t0 = time.time()
        folder = core.separate_to_folder(tmp / "test.wav", "htdemucs", say, lambda p: None)
        stems = core.song_stems(folder)
        assert {"vocals", "drums", "bass", "other"} <= set(stems), stems
        say(f"pemisahan AI: OK {stems} dalam {time.time() - t0:.1f} dtk")

        out = core.make_variant(folder, 2, 1.25, say, lambda p: None)
        v = core.load_audio(out / "vocals.wav")
        expected = audio.shape[1] / 1.25
        assert abs(v.shape[1] - expected) < sr * 0.5, (v.shape, expected)
        say(f"pitch +2 & tempo 125%: OK {v.shape}")

        import json
        import urllib.request
        httpd = server.create_server(0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read()
        assert b"Stem Mixer" in html
        songs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/songs").read())
        assert any(s["id"] == folder.name for s in songs["songs"]), songs
        httpd.shutdown()
        say("server lokal: OK")

        if sys.platform == "win32":
            import webview  # noqa: F401
            import webview.platforms.winforms  # noqa: F401  (pythonnet + WinForms)
            say("komponen jendela (pywebview/WebView2): OK")

        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
        say("SELFTEST OK")
        return 0
    except Exception:
        say("SELFTEST GAGAL\n" + traceback.format_exc())
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    run_app()
