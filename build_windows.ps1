# StreamVault Windows EXE builder. Run via build_windows.bat.
param([switch]$Onedir)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Fail($Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " StreamVault 1.2 - Build Windows EXE " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$pythonCmd = $null
foreach ($cmd in @('py', 'python')) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $pythonCmd = $cmd
        break
    }
}
if (-not $pythonCmd) { Fail 'Python not found. Install Python 3.12 or 3.13 (64-bit) from python.org.' }

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '[1/5] Creating virtual environment...'
    if ($pythonCmd -eq 'py') { & py -3 -m venv .venv }
    else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { Fail 'Unable to create Python virtual environment.' }
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Write-Host '[2/5] Installing dependencies (requires Internet)...'
& $python -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail 'pip upgrade failed.' }
& $python -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'Python dependency installation failed.' }

New-Item -Path tools -ItemType Directory -Force | Out-Null
New-Item -Path '.build_downloads' -ItemType Directory -Force | Out-Null

function DownloadArchive($url, $file) {
    if (-not (Test-Path $file)) {
        Write-Host "Downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $file -MaximumRedirection 10
    }
}

if (-not ((Test-Path tools\ffmpeg.exe) -and (Test-Path tools\ffprobe.exe))) {
    Write-Host '[3/5] Downloading FFmpeg Essentials from gyan.dev...'
    $archive = '.build_downloads\ffmpeg-release-essentials.zip'
    DownloadArchive 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $archive
    $extract = '.build_downloads\ffmpeg_extract'
    Expand-Archive -Path $archive -DestinationPath $extract -Force
    foreach ($exe in @('ffmpeg.exe', 'ffprobe.exe', 'ffplay.exe')) {
        $found = Get-ChildItem $extract -Recurse -File -Filter $exe | Select-Object -First 1
        if ($found) { Copy-Item $found.FullName "tools\$exe" -Force }
    }
    if (-not ((Test-Path tools\ffmpeg.exe) -and (Test-Path tools\ffprobe.exe))) {
        Fail 'FFmpeg executables missing after extraction.'
    }
} else { Write-Host '[3/5] FFmpeg found.' }

if (-not (Test-Path tools\deno.exe)) {
    Write-Host '[4/5] Downloading Deno (YouTube JavaScript challenges)...'
    $archive = '.build_downloads\deno-windows.zip'
    DownloadArchive 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' $archive
    $extract = '.build_downloads\deno_extract'
    Expand-Archive -Path $archive -DestinationPath $extract -Force
    $found = Get-ChildItem $extract -Recurse -File -Filter 'deno.exe' | Select-Object -First 1
    if (-not $found) { Fail 'Deno executable missing after extraction.' }
    Copy-Item $found.FullName 'tools\deno.exe' -Force
} else { Write-Host '[4/5] Deno found.' }

Write-Host '[5/5] Building with PyInstaller...'
$pyiArgs = @(
    '--noconfirm', '--clean', '--windowed',
    '--name', 'StreamVault',
    '--icon', 'assets\streamvault.ico',
    '--collect-all', 'yt_dlp_ejs',
    '--collect-submodules', 'yt_dlp',
    '--add-binary', 'tools\ffmpeg.exe;tools',
    '--add-binary', 'tools\ffprobe.exe;tools',
    '--add-binary', 'tools\deno.exe;tools',
    '--add-data', 'assets\streamvault.ico;assets'
)
if (Test-Path tools\ffplay.exe) { $pyiArgs += @('--add-binary', 'tools\ffplay.exe;tools') }
if ($Onedir) { $pyiArgs += '--onedir' }
else { $pyiArgs += '--onefile' }
$pyiArgs += 'app.py'
& $python -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) { Fail 'PyInstaller build failed. See output for details.' }

Write-Host "" 
if ($Onedir) {
    Write-Host "SUCCESS: dist\StreamVault\StreamVault.exe" -ForegroundColor Green
} else {
    Write-Host "SUCCESS: dist\StreamVault.exe" -ForegroundColor Green
}
Write-Host 'Copy the EXE to a Windows 10/11 computer and run it.'
