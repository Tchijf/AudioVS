# Audio Visualizer Studio 2.1 – Windows Release

Diese Version ist als echte Windows-Desktop-Anwendung vorbereitet.

## Endnutzer-Dateien

Nach dem Release-Build entstehen genau diese Auslieferungsdateien:

- `release\AudioVisualizerStudio_Setup.exe` – normale Windows-Installation
- `release\AudioVisualizerStudio_Portable.zip` – portable Version für USB/ohne Installation

Endnutzer benötigen **weder Python noch FFmpeg noch Internetzugang**.
Die Video-Engine wird beim Release-Build in `AudioVisualizerStudio.exe` eingebettet.

## Installierte Struktur

Programm:

`C:\Program Files\Audio Visualizer Studio\AudioVisualizerStudio.exe`

Benutzerdaten:

`%LOCALAPPDATA%\AudioVisualizerStudio`

Dort liegen unter anderem Presets, Autosave, importierte Logos/Cover und Theme-Einstellungen.
Ein Programm-Update ersetzt nur die Dateien unter `Program Files`.

## Release bauen

Auf einem Windows-10/11-Build-PC:

1. Python 3.11+ installieren (nur für den Build-PC).
2. `build_release_windows.bat` doppelklicken.
3. Der Builder lädt die Video-Engine **nur für den Build**, baut die selbstständige EXE, erstellt Portable ZIP und Setup.
4. Falls Inno Setup 6 fehlt und `winget` verfügbar ist, wird Inno Setup automatisch auf dem Build-PC eingerichtet.

Der fertige Nutzer-PC lädt später nichts nach.

## Installer

Der Installer bietet den Ablauf:

**Willkommen → Installationsort → Desktop/Startmenü → Installation → Fertig**

Die App-ID bleibt versionsübergreifend gleich. Dadurch erkennt v2.2/v2.3 später eine vorhandene Installation und aktualisiert sie anstatt eine zweite Kopie anzulegen.

Bei der Deinstallation werden persönliche Daten standardmäßig **nicht** gelöscht. Der Uninstaller fragt ausdrücklich danach, wobei „Nein“ die Standardauswahl ist.

## Code Signing

Siehe `SIGNING_README.md`. Der Builder kann App und Setup automatisch mit einem eigenen PFX-Code-Signing-Zertifikat signieren.
