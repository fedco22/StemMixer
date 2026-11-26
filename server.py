"""
Stem Mixer – versi Web Lokal
============================
Menjalankan server kecil di PC Anda sendiri (http://localhost:8765) dan membuka
antarmukanya di browser. Semua proses tetap di PC Anda, tidak ada yang diunggah
ke internet.

Jalankan:  python server.py
"""

import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import core

HOST, PORT = "127.0.0.1", 8765
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
INDEX = HERE / "web" / "index.html"
UPLOADS = core.APP_DIR / "_uploads"

JOBS = {}
JOBS_LOCK = threading.Lock()
WORK_LOCK = threading.Lock()          # satu proses berat dalam satu waktu


# ----------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------
def new_job(target, *args):
    jid = uuid.uuid4().hex[:12]
    job = {"state": "queued", "progress": 0.0, "message": "Menunggu antrean…",
           "result": None, "error": None}
    with JOBS_LOCK:
        JOBS[jid] = job

    def status(msg):
        job["message"] = msg

    def progress(p):
        job["progress"] = float(p)

    def run():
        with WORK_LOCK:
            job["state"] = "running"
            try:
                job["result"] = target(status, progress, *args)
                job["progress"] = 1.0
                job["message"] = "Selesai."
                job["state"] = "done"
            except Exception:
                job["error"] = traceback.format_exc()[-2000:]
                job["message"] = "Terjadi kesalahan."
                job["state"] = "error"

    threading.Thread(target=run, daemon=True).start()
    return jid


def stems_payload(song_id, variant):
    folder = song_path(song_id)
    stems = core.song_stems(folder)
    return {"song": song_id, "variant": variant,
            "stems": [{"name": s, "label": core.STEM_LABELS.get(s, s.capitalize()),
                       "url": f"/stems/{quote_seg(song_id)}/{variant}/{s}.wav"}
                      for s in stems]}


def quote_seg(s):
    from urllib.parse import quote
    return quote(s, safe="")


def song_path(song_id):
    p = (core.APP_DIR / song_id).resolve()
    if p.parent != core.APP_DIR.resolve() or not p.is_dir():
        raise FileNotFoundError(song_id)
    return p


def stem_file(song_id, variant, stem):
    """Path aman ke file stem; FileNotFoundError bila tidak valid/tidak ada."""
    if not re.fullmatch(r"\w+", stem) or not re.fullmatch(r"orig|pt_[+-]\d+_\d+", variant):
        raise FileNotFoundError(stem)
    folder = song_path(song_id)
    path = folder / f"{stem}.wav" if variant == "orig" else folder / "_variants" / variant / f"{stem}.wav"
    if not path.is_file():
        raise FileNotFoundError(stem)
    return path


def job_separate(status, progress, src, model):
    folder = core.song_dir_for(src, model)
    if not (folder / "info.json").exists():
        folder = core.separate_to_folder(src, model, status, progress)
    try:
        src.unlink()
    except OSError:
        pass
    return stems_payload(folder.name, "orig")


def job_variant(status, progress, song_id, semis, speed):
    core.make_variant(song_path(song_id), semis, speed, status, progress)
    return stems_payload(song_id, core.variant_name(semis, speed))


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "StemMixer/1.0"

    def log_message(self, fmt, *args):
        pass  # jangan penuhi konsol

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, ctype):
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1024 * 1024)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.split("/") if p]
            qs = parse_qs(url.query)

            if not parts:
                return self._file(INDEX, "text/html; charset=utf-8")

            if parts == ["api", "songs"]:
                return self._json({"songs": core.list_songs(),
                                   "models": core.MODELS})

            if parts == ["api", "job"]:
                job = JOBS.get(qs.get("id", [""])[0])
                if not job:
                    return self._json({"error": "job tidak ditemukan"}, 404)
                return self._json(job)

            if len(parts) == 4 and parts[0] == "stems":
                _, song_id, variant, fname = parts
                if not fname.endswith(".wav"):
                    return self._json({"error": "nama tidak valid"}, 400)
                return self._file(stem_file(song_id, variant, fname[:-4]), "audio/wav")

            self._json({"error": "tidak ditemukan"}, 404)
        except FileNotFoundError:
            self._json({"error": "file tidak ditemukan"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json({"error": traceback.format_exc()[-1500:]}, 500)

    def do_POST(self):
        try:
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            length = int(self.headers.get("Content-Length", 0))

            if url.path == "/api/separate":
                model = qs.get("model", ["htdemucs"])[0]
                if model not in core.MODELS:
                    return self._json({"error": "model tidak dikenal"}, 400)
                name = unquote(self.headers.get("X-Filename", "lagu.mp3"))
                name = core.safe_name(Path(name).name)
                UPLOADS.mkdir(parents=True, exist_ok=True)
                dest = UPLOADS / f"{uuid.uuid4().hex[:6]}_{name}"
                remaining = length
                with open(dest, "wb") as f:
                    while remaining > 0:
                        chunk = self.rfile.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                # nama folder memakai nama asli (tanpa prefix acak)
                src = UPLOADS / name if not (UPLOADS / name).exists() else dest
                if src != dest:
                    dest.rename(src)
                return self._json({"job": new_job(job_separate, src, model)})

            body = json.loads(self.rfile.read(length) or b"{}")

            if url.path == "/api/variant":
                song_id = body["song"]
                semis = int(body.get("semitones", 0))
                speed = float(body.get("tempo", 100)) / 100.0
                if not (-12 <= semis <= 12 and 0.5 <= speed <= 1.5):
                    return self._json({"error": "di luar batas"}, 400)
                song_path(song_id)
                variant = core.variant_name(semis, speed)
                if variant == "orig":
                    return self._json({"ready": stems_payload(song_id, "orig")})
                return self._json({"job": new_job(job_variant, song_id, semis, speed)})

            if url.path == "/api/delete":
                folder = song_path(body["song"])
                shutil.rmtree(folder, ignore_errors=True)
                return self._json({"ok": True})

            self._json({"error": "tidak ditemukan"}, 404)
        except FileNotFoundError:
            self._json({"error": "lagu tidak ditemukan"}, 404)
        except Exception:
            self._json({"error": traceback.format_exc()[-1500:]}, 500)


def create_server(port=0):
    """Buat server HTTP lokal. port=0 -> pilih port kosong otomatis."""
    core.APP_DIR.mkdir(parents=True, exist_ok=True)
    return ThreadingHTTPServer((HOST, port), Handler)


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    core.APP_DIR.mkdir(parents=True, exist_ok=True)

    try:
        httpd = create_server(PORT)
    except OSError:
        # Server kemungkinan sudah berjalan: cukup buka browser
        webbrowser.open(f"http://localhost:{PORT}")
        return

    url = f"http://localhost:{PORT}"
    print("=" * 56)
    print(f"  Stem Mixer berjalan di  {url}")
    print("  Biarkan jendela ini terbuka selama memakai aplikasi.")
    print("  Tutup jendela ini untuk mematikan aplikasi.")
    print("=" * 56)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
