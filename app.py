"""
Stem Mixer - Vocal Remover, Stem Splitter, Pitch & Tempo
=========================================================
- Memisahkan lagu menjadi stem (vokal, drum, bass, lainnya / + gitar, piano)
  memakai Demucs (AI dari Meta).
- Atur volume tiap stem 0-150% (0 = hilang total), Mute, Solo.
- Ubah pitch (key) -12..+12 semitone dan tempo 50-150% tanpa mengubah satu sama lain.
- Ekspor hasil mix atau tiap stem ke WAV.

Jalankan:  python app.py
"""

import json
import queue
import re
import sys
import threading
import traceback
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from core import (SR, APP_DIR, STEM_LABELS, MODELS, load_audio, save_wav,
                  separate, process_stem, sort_stems, safe_name)

AUDIO_TYPES = [
    ("File audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.aiff *.aif"),
    ("Semua file", "*.*"),
]




def fmt_time(samples):
    s = int(samples / SR)
    return f"{s // 60}:{s % 60:02d}"


# ----------------------------------------------------------------------------
# Real-time mixer (sounddevice)
# ----------------------------------------------------------------------------
class Mixer:
    def __init__(self):
        self.lock = threading.Lock()
        self.names = []
        self.tracks = []          # list of (2, n) float32
        self.length = 0
        self.gains = np.zeros(0, np.float32)
        self.master = 1.0
        self.pos = 0
        self.playing = False
        self.stream = None

    def start_stream(self):
        import sounddevice as sd

        self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32",
                                      blocksize=1024, callback=self._callback)
        self.stream.start()

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass

    def set_tracks(self, names, tracks, pos_scale=None):
        length = min(t.shape[1] for t in tracks) if tracks else 0
        with self.lock:
            if pos_scale is not None:
                self.pos = int(self.pos * pos_scale)
            else:
                self.pos = 0
            self.names = list(names)
            self.tracks = list(tracks)
            self.length = length
            if len(self.gains) != len(names):
                self.gains = np.ones(len(names), np.float32)
            self.pos = max(0, min(self.pos, max(length - 1, 0)))

    def set_gains(self, gains):
        with self.lock:
            self.gains = np.asarray(gains, np.float32)

    def seek(self, frac):
        with self.lock:
            self.pos = int(max(0.0, min(1.0, frac)) * max(self.length - 1, 0))

    def _callback(self, outdata, frames, time_info, status):
        with self.lock:
            if not self.playing or not self.tracks:
                outdata.fill(0)
                return
            start = self.pos
            end = min(start + frames, self.length)
            cnt = end - start
            mix = np.zeros((2, cnt), np.float32)
            for g, t in zip(self.gains, self.tracks):
                if g > 0:
                    mix += t[:, start:end] * (g * self.master)
            np.clip(mix, -1.0, 1.0, out=mix)
            outdata[:cnt] = mix.T
            outdata[cnt:] = 0
            self.pos = end
            if end >= self.length:
                self.playing = False
                self.pos = 0

    def render_mix(self):
        with self.lock:
            out = np.zeros((2, self.length), np.float32)
            for g, t in zip(self.gains, self.tracks):
                if g > 0:
                    out += t[:, :self.length] * (g * self.master)
        return out


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Stem Mixer – Vocal Remover, Splitter, Pitch & Tempo")
        root.geometry("900x680")
        root.minsize(760, 560)

        self.q = queue.Queue()
        self.mixer = Mixer()
        self.original = {}        # stem -> array asli (tanpa pitch/tempo)
        self.stem_vars = {}       # stem -> dict(vol, mute, solo, label)
        self.song_title = None
        self.busy = False
        self.dragging = False
        self.applied_semis = 0
        self.applied_speed = 1.0

        self._build_ui()
        try:
            self.mixer.start_stream()
        except Exception as e:
            messagebox.showerror("Audio", f"Tidak bisa membuka perangkat audio:\n{e}")

        root.bind("<space>", lambda e: self.toggle_play())
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll_queue)
        root.after(200, self._tick)

    # ---------------- UI layout ----------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Big.TButton", font=("Segoe UI", 10, "bold"))

        # --- Baris file ---
        top = ttk.LabelFrame(self.root, text="1. Lagu")
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Model:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.model_var = tk.StringVar(value=MODELS["htdemucs"])
        self.model_box = ttk.Combobox(top, textvariable=self.model_var, state="readonly",
                                      values=list(MODELS.values()), width=44)
        self.model_box.grid(row=0, column=1, sticky="w", padx=6)

        self.btn_open = ttk.Button(top, text="Buka Lagu & Pisahkan…", style="Big.TButton",
                                   command=self.open_song)
        self.btn_open.grid(row=0, column=2, padx=6)
        self.btn_open_stems = ttk.Button(top, text="Buka Folder Stem…",
                                         command=self.open_stem_folder)
        self.btn_open_stems.grid(row=0, column=3, padx=6)

        self.song_label = ttk.Label(top, text="Belum ada lagu.", style="Title.TLabel")
        self.song_label.grid(row=1, column=0, columnspan=4, sticky="w", padx=6)

        self.progress = ttk.Progressbar(top, maximum=1.0, mode="determinate")
        self.progress.grid(row=2, column=0, columnspan=4, sticky="ew", padx=6, pady=(4, 2))
        self.status = ttk.Label(top, text="Siap.")
        self.status.grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6))
        top.columnconfigure(1, weight=1)

        # --- Mixer stem ---
        mid = ttk.LabelFrame(self.root, text="2. Mixer Stem  (geser ke 0 = suara hilang)")
        mid.pack(fill="both", expand=True, **pad)

        presets = ttk.Frame(mid)
        presets.pack(fill="x", padx=6, pady=4)
        ttk.Button(presets, text="Karaoke (hapus vokal)",
                   command=lambda: self.preset("karaoke")).pack(side="left", padx=3)
        ttk.Button(presets, text="Hanya vokal (acapella)",
                   command=lambda: self.preset("acapella")).pack(side="left", padx=3)
        ttk.Button(presets, text="Tanpa bass",
                   command=lambda: self.preset("nobass")).pack(side="left", padx=3)
        ttk.Button(presets, text="Tanpa drum",
                   command=lambda: self.preset("nodrums")).pack(side="left", padx=3)
        ttk.Button(presets, text="Reset semua 100%",
                   command=lambda: self.preset("reset")).pack(side="left", padx=3)

        self.stem_frame = ttk.Frame(mid)
        self.stem_frame.pack(fill="both", expand=True, padx=6, pady=4)
        ttk.Label(self.stem_frame,
                  text="Buka lagu terlebih dulu. Stem akan muncul di sini.").pack(pady=20)

        master_row = ttk.Frame(mid)
        master_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(master_row, text="Master", width=10,
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        self.master_var = tk.IntVar(value=100)
        tk.Scale(master_row, from_=0, to=150, orient="horizontal", variable=self.master_var,
                 showvalue=True, length=420, command=lambda v: self._update_gains()
                 ).pack(side="left", fill="x", expand=True)
        ttk.Label(master_row, text="%").pack(side="left")

        # --- Pitch & tempo ---
        pt = ttk.LabelFrame(self.root, text="3. Pitch (Key) & Tempo")
        pt.pack(fill="x", **pad)

        ttk.Label(pt, text="Pitch (semitone):").grid(row=0, column=0, sticky="w", padx=6)
        self.semi_var = tk.IntVar(value=0)
        tk.Scale(pt, from_=-12, to=12, orient="horizontal", variable=self.semi_var,
                 resolution=1, length=300, command=lambda v: self._pt_changed()
                 ).grid(row=0, column=1, sticky="ew", padx=6)
        self.semi_label = ttk.Label(pt, text="Key asli", width=26)
        self.semi_label.grid(row=0, column=2, sticky="w")

        ttk.Label(pt, text="Tempo (%):").grid(row=1, column=0, sticky="w", padx=6)
        self.tempo_var = tk.IntVar(value=100)
        tk.Scale(pt, from_=50, to=150, orient="horizontal", variable=self.tempo_var,
                 resolution=1, length=300, command=lambda v: self._pt_changed()
                 ).grid(row=1, column=1, sticky="ew", padx=6)
        self.tempo_label = ttk.Label(pt, text="Tempo asli", width=26)
        self.tempo_label.grid(row=1, column=2, sticky="w")

        btns = ttk.Frame(pt)
        btns.grid(row=0, column=3, rowspan=2, padx=8)
        self.btn_apply = ttk.Button(btns, text="Terapkan", style="Big.TButton",
                                    command=self.apply_pitch_tempo)
        self.btn_apply.pack(fill="x", pady=2)
        ttk.Button(btns, text="Reset", command=self.reset_pitch_tempo).pack(fill="x", pady=2)
        pt.columnconfigure(1, weight=1)

        # --- Transport & ekspor ---
        bottom = ttk.LabelFrame(self.root, text="4. Putar & Ekspor")
        bottom.pack(fill="x", **pad)

        self.btn_play = ttk.Button(bottom, text="▶ Putar", width=10, command=self.toggle_play)
        self.btn_play.grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(bottom, text="■ Stop", width=8, command=self.stop).grid(row=0, column=1)

        self.seek_var = tk.DoubleVar(value=0)
        self.seek = ttk.Scale(bottom, from_=0, to=1000, orient="horizontal",
                              variable=self.seek_var)
        self.seek.grid(row=0, column=2, sticky="ew", padx=8)
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "dragging", True))
        self.seek.bind("<ButtonRelease-1>", self._seek_release)
        self.time_label = ttk.Label(bottom, text="0:00 / 0:00", width=12)
        self.time_label.grid(row=0, column=3, padx=6)

        ttk.Button(bottom, text="Ekspor Mix (WAV)…",
                   command=self.export_mix).grid(row=0, column=4, padx=4)
        ttk.Button(bottom, text="Ekspor Semua Stem…",
                   command=self.export_stems).grid(row=0, column=5, padx=(4, 6))
        bottom.columnconfigure(2, weight=1)

    # ---------------- Stem rows ----------------
    def _build_stem_rows(self, names):
        for w in self.stem_frame.winfo_children():
            w.destroy()
        self.stem_vars = {}
        for r, name in enumerate(names):
            label = STEM_LABELS.get(name, name.capitalize())
            vol = tk.IntVar(value=100)
            mute = tk.BooleanVar(value=False)
            solo = tk.BooleanVar(value=False)
            ttk.Label(self.stem_frame, text=label, width=10,
                      font=("Segoe UI", 10, "bold")).grid(row=r, column=0, sticky="w")
            tk.Scale(self.stem_frame, from_=0, to=150, orient="horizontal", variable=vol,
                     showvalue=True, length=420,
                     command=lambda v: self._update_gains()
                     ).grid(row=r, column=1, sticky="ew", padx=6)
            ttk.Label(self.stem_frame, text="%").grid(row=r, column=2)
            ttk.Checkbutton(self.stem_frame, text="Mute", variable=mute,
                            command=self._update_gains).grid(row=r, column=3, padx=6)
            ttk.Checkbutton(self.stem_frame, text="Solo", variable=solo,
                            command=self._update_gains).grid(row=r, column=4, padx=6)
            self.stem_vars[name] = {"vol": vol, "mute": mute, "solo": solo}
        self.stem_frame.columnconfigure(1, weight=1)
        self._update_gains()

    def _update_gains(self):
        if not self.stem_vars:
            return
        any_solo = any(v["solo"].get() for v in self.stem_vars.values())
        gains = []
        for name in self.mixer.names:
            v = self.stem_vars.get(name)
            if v is None:
                gains.append(0.0)
                continue
            g = v["vol"].get() / 100.0
            if v["mute"].get() or (any_solo and not v["solo"].get()):
                g = 0.0
            gains.append(g)
        self.mixer.set_gains(gains)
        self.mixer.master = self.master_var.get() / 100.0

    def preset(self, kind):
        if not self.stem_vars:
            return
        for name, v in self.stem_vars.items():
            v["mute"].set(False)
            v["solo"].set(False)
            if kind == "karaoke":
                v["vol"].set(0 if name == "vocals" else 100)
            elif kind == "acapella":
                v["vol"].set(100 if name == "vocals" else 0)
            elif kind == "nobass":
                v["vol"].set(0 if name == "bass" else 100)
            elif kind == "nodrums":
                v["vol"].set(0 if name == "drums" else 100)
            else:
                v["vol"].set(100)
        self._update_gains()

    # ---------------- Pitch / tempo ----------------
    def _pt_changed(self):
        s = self.semi_var.get()
        t = self.tempo_var.get()
        self.semi_label.config(text="Key asli" if s == 0 else
                               f"{'+' if s > 0 else ''}{s} semitone "
                               f"({'naik' if s > 0 else 'turun'} {abs(s) / 2:g} nada)")
        self.tempo_label.config(text="Tempo asli" if t == 100 else
                                f"{t}% ({'lebih cepat' if t > 100 else 'lebih lambat'})")
        pending = (s != self.applied_semis or abs(t / 100 - self.applied_speed) > 1e-6)
        self.btn_apply.config(text="Terapkan *" if pending else "Terapkan")

    def reset_pitch_tempo(self):
        self.semi_var.set(0)
        self.tempo_var.set(100)
        self._pt_changed()
        self.apply_pitch_tempo()

    def apply_pitch_tempo(self):
        if not self.original or self.busy:
            return
        semis = self.semi_var.get()
        speed = self.tempo_var.get() / 100.0
        if semis == self.applied_semis and abs(speed - self.applied_speed) < 1e-6:
            return
        self._set_busy(True)
        threading.Thread(target=self._process_worker, args=(semis, speed), daemon=True).start()

    def _process_worker(self, semis, speed):
        try:
            names = sort_stems(self.original.keys())
            out = []
            for i, n in enumerate(names):
                self.q.put(("status", f"Mengubah pitch/tempo: {STEM_LABELS.get(n, n)}…"))
                out.append(process_stem(self.original[n], semis, speed))
                self.q.put(("progress", (i + 1) / len(names)))
            self.q.put(("processed", names, out, semis, speed))
        except Exception:
            self.q.put(("error", traceback.format_exc()))

    # ---------------- Open / separate ----------------
    def _selected_model(self):
        label = self.model_var.get()
        for k, v in MODELS.items():
            if v == label:
                return k
        return "htdemucs"

    def open_song(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="Pilih lagu", filetypes=AUDIO_TYPES)
        if not path:
            return
        path = Path(path)
        model = self._selected_model()
        cache = APP_DIR / f"{safe_name(path.stem)} [{model}]"
        if (cache / "info.json").exists():
            self.stop()
            self._load_folder(cache, title=path.stem)
            self.status.config(text=f"Dimuat dari hasil sebelumnya: {cache}")
            return
        self.stop()
        self._set_busy(True)
        self.progress.config(value=0)
        self.song_label.config(text=f"Memproses: {path.name}")
        threading.Thread(target=self._separate_worker, args=(path, model, cache),
                         daemon=True).start()

    def _separate_worker(self, path, model, cache):
        try:
            stems = separate(path, model,
                             status_cb=lambda s: self.q.put(("status", s)),
                             progress_cb=lambda p: self.q.put(("progress", p)))
            self.q.put(("status", "Menyimpan stem…"))
            cache.mkdir(parents=True, exist_ok=True)
            for name, audio in stems.items():
                save_wav(cache / f"{name}.wav", audio)
            (cache / "info.json").write_text(json.dumps(
                {"source": str(path), "model": model, "stems": list(stems)},
                ensure_ascii=False, indent=2), encoding="utf-8")
            self.q.put(("separated", stems, path.stem, cache))
        except Exception:
            self.q.put(("error", traceback.format_exc()))

    def open_stem_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(title="Pilih folder berisi file stem (.wav)",
                                         initialdir=str(APP_DIR) if APP_DIR.exists() else None)
        if folder:
            self.stop()
            self._load_folder(Path(folder))

    def _load_folder(self, folder, title=None):
        files = sorted(folder.glob("*.wav"))
        if not files:
            messagebox.showwarning("Folder stem", "Tidak ada file .wav di folder itu.")
            return
        try:
            stems = {f.stem: load_audio(f) for f in files}
        except Exception as e:
            messagebox.showerror("Gagal membuka", str(e))
            return
        if title is None:
            title = re.sub(r"\s*\[.*\]$", "", folder.name)
        self._set_stems(stems, title, folder)

    def _set_stems(self, stems, title, folder):
        self.original = stems
        self.applied_semis = 0
        self.applied_speed = 1.0
        self.semi_var.set(0)
        self.tempo_var.set(100)
        self._pt_changed()
        names = sort_stems(stems.keys())
        self.mixer.set_tracks(names, [stems[n] for n in names])
        self._build_stem_rows(names)
        self.song_title = title
        self.song_label.config(text=f"{title}   ({len(names)} stem)")
        self.status.config(text=f"Siap. Stem tersimpan di: {folder}")
        self.progress.config(value=1.0)

    # ---------------- Transport ----------------
    def toggle_play(self):
        if not self.mixer.tracks:
            return
        self.mixer.playing = not self.mixer.playing

    def stop(self):
        self.mixer.playing = False
        self.mixer.seek(0)

    def _seek_release(self, event):
        self.dragging = False
        self.mixer.seek(self.seek_var.get() / 1000.0)

    def _tick(self):
        m = self.mixer
        self.btn_play.config(text="❚❚ Jeda" if m.playing else "▶ Putar")
        if m.length:
            if not self.dragging:
                self.seek_var.set(m.pos / m.length * 1000)
            self.time_label.config(text=f"{fmt_time(m.pos)} / {fmt_time(m.length)}")
        self.root.after(200, self._tick)

    # ---------------- Export ----------------
    def _suffix(self):
        parts = []
        if self.applied_semis:
            parts.append(f"{'+' if self.applied_semis > 0 else ''}{self.applied_semis}st")
        if abs(self.applied_speed - 1) > 1e-6:
            parts.append(f"{round(self.applied_speed * 100)}pct")
        return (" " + " ".join(parts)) if parts else ""

    def export_mix(self):
        if not self.mixer.tracks:
            return
        path = filedialog.asksaveasfilename(
            title="Simpan mix", defaultextension=".wav", filetypes=[("WAV", "*.wav")],
            initialfile=f"{safe_name(self.song_title or 'mix')} - mix{self._suffix()}.wav")
        if not path:
            return
        mix = self.mixer.render_mix()
        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        save_wav(path, mix)
        msg = f"Tersimpan:\n{path}"
        if peak > 1.0:
            msg += "\n\nCatatan: volume terlalu tinggi sehingga ada clipping. " \
                   "Turunkan Master lalu ekspor ulang bila perlu."
        messagebox.showinfo("Ekspor mix", msg)

    def export_stems(self):
        if not self.mixer.tracks:
            return
        folder = filedialog.askdirectory(title="Pilih folder tujuan")
        if not folder:
            return
        base = f"{safe_name(self.song_title or 'lagu')}{self._suffix()}"
        for name, track in zip(self.mixer.names, self.mixer.tracks):
            save_wav(Path(folder) / f"{base} - {STEM_LABELS.get(name, name)}.wav", track)
        messagebox.showinfo("Ekspor stem", f"{len(self.mixer.names)} stem tersimpan di:\n{folder}")

    # ---------------- Background queue ----------------
    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_open, self.btn_open_stems, self.btn_apply):
            b.config(state=state)
        self.root.config(cursor="watch" if busy else "")

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.status.config(text=msg[1])
                elif kind == "progress":
                    self.progress.config(value=msg[1])
                elif kind == "separated":
                    _, stems, title, folder = msg
                    self._set_busy(False)
                    self._set_stems(stems, title, folder)
                elif kind == "processed":
                    _, names, tracks, semis, speed = msg
                    scale = self.applied_speed / speed
                    self.mixer.set_tracks(names, tracks, pos_scale=scale)
                    self._update_gains()
                    self.applied_semis, self.applied_speed = semis, speed
                    self._pt_changed()
                    self._set_busy(False)
                    self.status.config(text="Pitch & tempo diterapkan.")
                elif kind == "error":
                    self._set_busy(False)
                    self.status.config(text="Terjadi kesalahan.")
                    messagebox.showerror("Kesalahan", msg[1][-1500:])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_close(self):
        self.mixer.playing = False
        self.mixer.close()
        self.root.destroy()


def check_dependencies():
    missing = []
    for mod, pkg in [("sounddevice", "sounddevice"), ("pedalboard", "pedalboard"),
                     ("demucs", "demucs"), ("torch", "torch")]:
        try:
            __import__(mod)
        except Exception:
            missing.append(pkg)
    return missing


def main():
    # Di mode tanpa konsol (.exe / pythonw), stdout/stderr = None dan progress
    # download model akan crash. Alihkan ke devnull.
    import os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    root = tk.Tk()
    missing = check_dependencies()
    if missing:
        root.withdraw()
        messagebox.showerror(
            "Library belum terpasang",
            "Library berikut belum terpasang:\n  " + ", ".join(missing) +
            "\n\nJalankan install.bat (Windows) atau:\n"
            "  pip install -r requirements.txt")
        root.destroy()
        sys.exit(1)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
