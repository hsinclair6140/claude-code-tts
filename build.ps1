# Builds dist\claude-code-tts.exe (portable app) and dist\ClaudeCodeTTS-Setup-<version>.exe (installer).
# Requires: Python 3.10+ (py launcher) and Inno Setup 6.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$version = (Select-String -Path src\claude_tts.py -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building Claude Code TTS $version"

if (-not (Test-Path .venv)) { py -3 -m venv .venv }
.\.venv\Scripts\python -m pip install --quiet --upgrade pip pyinstaller

.\.venv\Scripts\python -m unittest discover tests
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }

.\.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name claude-code-tts `
    --add-data "$PSScriptRoot\assets\speak.ps1;." `
    --distpath dist --workpath build --specpath build src\claude_tts.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup' }

& $iscc /Q "/DAppVersion=$version" installer\setup.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }

Get-ChildItem dist\*.exe | Format-Table Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }
