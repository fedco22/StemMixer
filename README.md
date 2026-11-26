# Stem Mixer

Aplikasi Windows untuk **menghapus vokal**, **memisahkan instrumen** (vokal, drum, bass, lainnya — atau 6 stem dengan gitar & piano), **mengatur volume tiap instrumen** sampai hilang, serta **mengubah key (pitch) dan tempo**. Semua diproses di PC Anda dengan AI [Demucs](https://github.com/facebookresearch/demucs).

## Unduh & pasang (tanpa Python)

1. Buka halaman **[Releases](../../releases/latest)** dan unduh `StemMixer-Setup.exe`.
2. Jalankan file tersebut. Jika muncul *"Windows protected your PC"*, klik **More info → Run anyway** (aplikasi belum ditandatangani secara digital).
3. Ikuti petunjuk instalasi, lalu buka **Stem Mixer** dari Desktop atau Start Menu.

Butuh Windows 10/11 64-bit. Pemisahan memakai CPU: lagu 4 menit ±3–10 menit, tergantung PC. Lagu yang sudah diproses tersimpan di Pustaka dan bisa dibuka lagi dengan cepat.

## Fitur

- Tarik lagu (mp3, wav, flac, m4a, ogg) ke jendela aplikasi.
- Mixer per stem 0–150 %, Mute, Solo, tombol cepat Karaoke / Hanya vokal / Tanpa bass / Tanpa drum.
- Pitch −12…+12 semitone dan tempo 50–150 %, saling independen.
- Simpan hasil mix atau tiap stem ke WAV.

## Untuk pengembang

| File | Isi |
|---|---|
| `stemmixer_app.py` | Aplikasi berjendela (pywebview) + `--selftest` |
| `server.py` | Server lokal (juga bisa dijalankan sendiri: `python server.py`) |
| `core.py` | Demucs, pitch/tempo (pedalboard), pustaka lagu |
| `web/index.html` | Antarmuka |
| `app.py` | Versi desktop Tkinter lama |
| `.github/workflows/build.yml` | Build installer otomatis setiap push ke `main` |
