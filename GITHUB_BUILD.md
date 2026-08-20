# Windows-Release über GitHub Actions

Die enthaltene Workflow-Datei `.github/workflows/windows-release.yml` kann auf
einem Windows-GitHub-Runner automatisch die zwei Endnutzer-Dateien erzeugen:

- `AudioVisualizerStudio_Setup.exe`
- `AudioVisualizerStudio_Portable.zip`

Nach `Run workflow` liegt das Ergebnis als Workflow-Artefakt
`AudioVisualizerStudio-Windows-2.1` vor.

Das ist besonders praktisch, wenn der Entwicklungsrechner kein Windows ist.
Code-Signing kann später über verschlüsselte GitHub-Secrets ergänzt werden.
