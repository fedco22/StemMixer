@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Jalankan install.bat terlebih dahulu.
  pause
  exit /b 1
)
title Stem Mixer (web lokal) - tutup jendela ini untuk mematikan
venv\Scripts\python.exe server.py
pause
