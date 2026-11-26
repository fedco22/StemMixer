@echo off
cd /d "%~dp0"
echo === Membuat file .exe Stem Mixer ===
if not exist venv\Scripts\python.exe (
  echo Jalankan install.bat terlebih dahulu.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
pip install pyinstaller

echo.
echo Pilih versi yang ingin dibuat:
echo   1 = Versi WEB lokal (dibuka di browser)  [disarankan]
echo   2 = Versi desktop (jendela biasa)
set /p CH=Pilihan [1/2]:

set COMMON=--noconfirm --clean ^
  --collect-all demucs --collect-all pedalboard --collect-all sounddevice ^
  --collect-all julius --collect-all einops --collect-all openunmix ^
  --collect-submodules torch

if "%CH%"=="2" (
  set OUT=dist\StemMixer
  pyinstaller %COMMON% --windowed --name StemMixer app.py
) else (
  set OUT=dist\StemMixerWeb
  pyinstaller %COMMON% --console --name StemMixerWeb --add-data "web;web" server.py
)

if errorlevel 1 (
  echo Gagal membuat exe. Kirim teks error di atas ke Claude.
  pause
  exit /b 1
)
echo.
echo Selesai! Aplikasi ada di folder:  %OUT%
echo Folder itu bisa di-zip dan dipakai di PC Windows lain tanpa Python.
echo Jangan pindahkan file .exe sendirian keluar dari foldernya; buat shortcut saja.
explorer %OUT%
pause
