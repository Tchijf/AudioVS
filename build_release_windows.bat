@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Audio Visualizer Studio 2.1 - Release Builder

set "PRODUCT=Audio Visualizer Studio"
set "VERSION=2.1.0"
set "APP_EXE=AudioVisualizerStudio.exe"
set "SETUP_EXE=AudioVisualizerStudio_Setup.exe"
set "PORTABLE_ZIP=AudioVisualizerStudio_Portable.zip"

echo.
echo ============================================================
echo   Audio Visualizer Studio 2.1 - Release Builder
echo ============================================================
echo.
echo Ausgabe fuer Endnutzer:
echo   release\%SETUP_EXE%
echo   release\%PORTABLE_ZIP%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [FEHLER] Fuer den Build-PC wird Python 3.11+ benoetigt.
  echo Endnutzer benoetigen Python NICHT.
  pause
  exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist release rmdir /s /q release
mkdir release >nul 2>nul

echo [1/6] Video-Engine fuer Offline-Auslieferung vorbereiten ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%cd%\vendor_ffmpeg_windows.ps1"
if errorlevel 1 (
  echo [FEHLER] Die Video-Engine konnte nicht fuer den Release-Build vorbereitet werden.
  pause
  exit /b 1
)
if not exist "ffmpeg\ffmpeg.exe" goto :missing_engine
if not exist "ffmpeg\ffprobe.exe" goto :missing_engine

echo [2/6] Build-Werkzeuge vorbereiten ...
python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 (
  echo [FEHLER] PyInstaller/Pillow konnten auf dem Build-PC nicht eingerichtet werden.
  pause
  exit /b 1
)

echo [3/6] Selbststaendige Windows-EXE bauen ...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name AudioVisualizerStudio ^
  --icon "assets\app_icon.ico" ^
  --version-file "version_info.txt" ^
  --add-data "assets;assets" ^
  --add-data "ffmpeg;engine" ^
  app.py
if errorlevel 1 goto :build_failed
if not exist "dist\%APP_EXE%" goto :build_failed

call :sign_if_configured "dist\%APP_EXE%"

echo [4/6] Portable Version erstellen ...
mkdir "release\portable" >nul 2>nul
copy /y "dist\%APP_EXE%" "release\portable\%APP_EXE%" >nul
copy /y "THIRD_PARTY_NOTICES.txt" "release\portable\THIRD_PARTY_NOTICES.txt" >nul
copy /y "README_ENDUSER.txt" "release\portable\README.txt" >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'release\portable\*' -DestinationPath 'release\%PORTABLE_ZIP%' -Force"
if errorlevel 1 (
  echo [FEHLER] Portable ZIP konnte nicht erstellt werden.
  pause
  exit /b 1
)
rmdir /s /q "release\portable"

echo [5/6] Inno Setup suchen ...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  where winget >nul 2>nul
  if not errorlevel 1 (
    echo Inno Setup 6 fehlt. Es wird nur auf dem Build-PC eingerichtet ...
    winget install --id JRSoftware.InnoSetup --exact --silent --accept-package-agreements --accept-source-agreements
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
    if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
  )
)
if not defined ISCC (
  echo [FEHLER] Inno Setup 6 wurde auf dem Build-PC nicht gefunden.
  echo Die selbststaendige App und Portable ZIP wurden trotzdem gebaut.
  pause
  exit /b 1
)

echo [6/6] Gebrandetes Installationsprogramm bauen ...
"%ISCC%" "installer\AudioVisualizerStudio.iss"
if errorlevel 1 (
  echo [FEHLER] Das Setup konnte nicht kompiliert werden.
  pause
  exit /b 1
)
if not exist "release\%SETUP_EXE%" (
  echo [FEHLER] Die erwartete Setup-Datei fehlt.
  pause
  exit /b 1
)
call :sign_if_configured "release\%SETUP_EXE%"

echo.
echo ============================================================
echo   RELEASE FERTIG
echo ============================================================
echo.
echo Normale Installation:
echo   release\%SETUP_EXE%
echo.
echo Portable Version:
echo   release\%PORTABLE_ZIP%
echo.
echo Endnutzer benoetigen weder Python noch FFmpeg noch Internet.
echo.
start "" explorer.exe "%cd%\release"
pause
exit /b 0

:missing_engine
echo [FEHLER] ffmpeg.exe/ffprobe.exe fehlen nach dem Vendor-Schritt.
pause
exit /b 1

:build_failed
echo [FEHLER] AudioVisualizerStudio.exe konnte nicht erstellt werden.
pause
exit /b 1

:sign_if_configured
set "TARGET_FILE=%~1"
if "%SIGN_PFX%"=="" (
  echo [Signing] Kein Zertifikat konfiguriert - Datei bleibt unsigniert.
  exit /b 0
)
if "%SIGN_PFX_PASSWORD%"=="" (
  echo [Signing] SIGN_PFX_PASSWORD fehlt - Datei bleibt unsigniert.
  exit /b 0
)
where signtool >nul 2>nul
if errorlevel 1 (
  echo [Signing] signtool.exe fehlt - Datei bleibt unsigniert.
  exit /b 0
)
echo [Signing] Signiere %TARGET_FILE% ...
signtool sign /fd SHA256 /f "%SIGN_PFX%" /p "%SIGN_PFX_PASSWORD%" /tr "http://timestamp.digicert.com" /td SHA256 "%TARGET_FILE%"
if errorlevel 1 (
  echo [WARNUNG] Code-Signing fehlgeschlagen.
) else (
  echo [OK] Signatur erfolgreich.
)
exit /b 0
