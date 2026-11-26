@echo off
cd /d "%~dp0"
echo === Stem Mixer: instalasi (sekali saja, perlu internet) ===
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3.11) else (set PY=python)
%PY% -m venv venv
if errorlevel 1 (
  echo Gagal membuat venv. Pastikan Python 3.11 terpasang dari python.org
  echo dan centang "Add Python to PATH" saat instal.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip

echo.
set /p GPU=Punya kartu grafis NVIDIA dan ingin pakai GPU (lebih cepat)? [y/N]:
if /i "%GPU%"=="y" (
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
)
pip install -r requirements.txt
echo.
echo Selesai! Jalankan aplikasi dengan klik dua kali run.bat
pause
