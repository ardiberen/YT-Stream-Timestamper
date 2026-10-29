@echo off
REM Builds Stream Timestamper into dist\StreamTimestamper\StreamTimestamper.exe
REM Usage:  build.bat            (folder build - recommended, starts fast, fewer antivirus false alarms)
REM         build.bat onefile    (single .exe, slower to start)
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo Python not found. Install Python 3.10+ and tick "Add python.exe to PATH". & exit /b 1)

if not exist venv (
  python -m venv venv || exit /b 1
)
call venv\Scripts\activate || exit /b 1
python -m pip install --upgrade pip >nul
pip install --upgrade -r requirements.txt || exit /b 1

REM yt-dlp-ejs must be installed: the app relies on it (nothing is downloaded at runtime)
python -c "import yt_dlp, yt_dlp_ejs, google.genai, customtkinter" || (echo A required package is missing. & exit /b 1)
REM record exactly which package versions went into this build (for audits / reproducing it)
pip freeze > build-versions.txt

REM Find deno.exe so it can be bundled inside the app (users then don't need to install Deno)
set DENO_ARG=
for /f "delims=" %%i in ('where deno 2^>nul') do (
  if not defined DENO_ARG set DENO_ARG=--add-binary "%%i;."
)
if not defined DENO_ARG (
  echo.
  echo WARNING: deno.exe not found, so it will NOT be bundled. Run:  winget install DenoLand.Deno
  echo          then open a new terminal and run build.bat again.
  echo.
)

REM Windows "Details" tab info (name, version, description) for the exe
python assets\make_version_info.py || exit /b 1

set MODE=--onedir
if /i "%1"=="onefile" set MODE=--onefile

pyinstaller --noconfirm --clean --windowed %MODE% --name StreamTimestamper ^
  --icon assets\icon.ico ^
  --version-file version_info.txt ^
  --add-data "assets;assets" ^
  --collect-all customtkinter ^
  --collect-all yt_dlp ^
  --collect-all yt_dlp_ejs ^
  --collect-all google.genai ^
  --collect-all certifi ^
  %DENO_ARG% ^
  app.py || exit /b 1

echo.
if /i "%1"=="onefile" (
  echo Done: dist\StreamTimestamper.exe
) else (
  echo Done: dist\StreamTimestamper\StreamTimestamper.exe  ^(zip the whole StreamTimestamper folder to share it^)
)
endlocal
