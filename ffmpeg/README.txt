Dieser Ordner wird nur auf dem Release-Build-PC verwendet.

`vendor_ffmpeg_windows.ps1` legt hier ffmpeg.exe und ffprobe.exe ab.
PyInstaller bettet den gesamten Ordner anschließend in AudioVisualizerStudio.exe
ein. Im installierten Endprodukt existiert kein separater FFmpeg-Setup-Schritt.
