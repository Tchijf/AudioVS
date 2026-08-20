param(
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = Join-Path $ProjectRoot 'ffmpeg'
$Ffmpeg = Join-Path $Target 'ffmpeg.exe'
$Ffprobe = Join-Path $Target 'ffprobe.exe'

if (-not $Force -and (Test-Path $Ffmpeg) -and (Test-Path $Ffprobe)) {
  Write-Host "[OK] Gebuendelte Video-Engine ist bereits vorhanden."
  exit 0
}

Write-Host "[Build] Video-Engine wird fuer die Release-Dateien geladen ..."
Write-Host "Dieser Download findet nur auf dem Build-PC statt; Endnutzer laden nichts nach."

$Url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ('avs_vendor_' + [Guid]::NewGuid().ToString('N'))
$Zip = Join-Path $TempRoot 'ffmpeg.zip'
$Extract = Join-Path $TempRoot 'extract'
New-Item -ItemType Directory -Force -Path $TempRoot, $Extract | Out-Null

try {
  $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($curl) {
    & curl.exe -L --fail --retry 3 --connect-timeout 20 -o $Zip $Url
    if ($LASTEXITCODE -ne 0) { throw "curl.exe Fehlercode $LASTEXITCODE" }
  } else {
    Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
  }

  Expand-Archive -Path $Zip -DestinationPath $Extract -Force
  $SourceFfmpeg = Get-ChildItem -Path $Extract -Filter 'ffmpeg.exe' -File -Recurse | Select-Object -First 1
  $SourceFfprobe = Get-ChildItem -Path $Extract -Filter 'ffprobe.exe' -File -Recurse | Select-Object -First 1
  if (-not $SourceFfmpeg -or -not $SourceFfprobe) {
    throw 'ffmpeg.exe/ffprobe.exe wurden im Download nicht gefunden.'
  }

  if (Test-Path $Target) { Remove-Item $Target -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $Target | Out-Null
  Copy-Item $SourceFfmpeg.FullName $Ffmpeg -Force
  Copy-Item $SourceFfprobe.FullName $Ffprobe -Force

  & $Ffmpeg -version | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'ffmpeg.exe konnte nicht validiert werden.' }
  & $Ffprobe -version | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'ffprobe.exe konnte nicht validiert werden.' }

  @"
Audio Visualizer Studio 2.1 - gebuendelte Video-Engine
Quelle: $Url
Build-Zeitpunkt: $(Get-Date -Format s)
Die Video-Engine wird in die Anwendungs-EXE eingebettet und nicht beim Endnutzer heruntergeladen.
Siehe THIRD_PARTY_NOTICES.txt.
"@ | Set-Content -Path (Join-Path $Target 'SOURCE.txt') -Encoding UTF8

  Write-Host "[OK] Video-Engine ist fuer den Release-Build bereit."
  exit 0
}
catch {
  Write-Host "[FEHLER] Video-Engine konnte nicht vorbereitet werden: $($_.Exception.Message)"
  exit 1
}
finally {
  Remove-Item -Path $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
