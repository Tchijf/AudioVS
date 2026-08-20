# Release-Checkliste 2.1

- [x] Endnutzer-Ausgabe `AudioVisualizerStudio_Setup.exe`
- [x] Zusätzlich `AudioVisualizerStudio_Portable.zip`
- [x] Gebrandeter Installer: Willkommen → Installationsort → Desktop/Startmenü → Installation → Fertig
- [x] Standardinstallation: `C:\Program Files\Audio Visualizer Studio\AudioVisualizerStudio.exe`
- [x] Python über PyInstaller unsichtbar in der App gebündelt
- [x] FFmpeg/ffprobe beim Release-Build in die App-EXE eingebettet
- [x] Kein FFmpeg-Download beim Endnutzer
- [x] C△D-Icon für EXE, Setup, Desktop, Startmenü und Apps/Deinstallation
- [x] Benutzerdaten ausschließlich unter `%LOCALAPPDATA%\AudioVisualizerStudio`
- [x] Normale Oberfläche zeigt nur `● Video-Engine bereit`
- [x] Technische Engine-Pfade nur in `Einstellungen – Erweitert – Diagnose`
- [x] Deinstaller fragt nach Benutzerdaten; Standardantwort ist Nein
- [x] Gleichbleibende AppId für In-place Updates
- [x] Migration der alten v2.0-Installation aus LocalAppData nach Program Files
- [x] Windows-Dateiversion/ProductVersion/Company/Icon im EXE-Resource-Block
- [x] Optionales Authenticode-Code-Signing vorbereitet
- [x] GitHub-Actions-Windows-Build vorbereitet
