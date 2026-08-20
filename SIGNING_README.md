# Windows Code Signing

Der Release-Builder ist für Authenticode-Signaturen vorbereitet.

Vor dem Start von `build_release_windows.bat` können auf dem Build-PC gesetzt werden:

```bat
set SIGN_PFX=C:\Sicher\AudioVisualizerStudio-CodeSigning.pfx
set SIGN_PFX_PASSWORD=DEIN_PASSWORT
build_release_windows.bat
```

Wenn das Windows SDK (`signtool.exe`) verfügbar ist, signiert der Builder sowohl
`AudioVisualizerStudio.exe` als auch `AudioVisualizerStudio_Setup.exe` mit SHA-256
und Zeitstempel.

Ohne Zertifikat werden beide Dateien regulär gebaut, bleiben aber unsigniert.
Windows SmartScreen kann bei neuen, unbekannten unsignierten Downloads eine Warnung anzeigen.
