# Claude Code TTS - Stop hook that reads Claude's last reply aloud, fully offline.
# Installed to ~/.claude/tts/speak.ps1 by the Claude Code TTS app. Settings live in
# config.json next to this script; edit them with the app rather than by hand.
#
#   speak.ps1            read a Stop-hook JSON payload from stdin and speak it
#   speak.ps1 -Stop      stop whatever is currently being read
#   speak.ps1 -Print     print the text that would be spoken instead of speaking it
param([switch]$Stop, [switch]$Print)

$ErrorActionPreference = 'SilentlyContinue'
$root    = $PSScriptRoot
$pidFile = Join-Path $env:TEMP 'claude_tts.pid'

function Stop-Speaking {
    if (Test-Path $pidFile) {
        $old = Get-Content $pidFile
        if ($old -and $old -ne $PID) { Stop-Process -Id $old -Force }
        Remove-Item $pidFile
    }
}

if ($Stop) { Stop-Speaking; exit 0 }

# ---- settings (defaults are used for anything missing from config.json) ----
$cfg = [ordered]@{
    enabled   = $true
    engine    = 'piper'             # 'piper' or 'sapi'
    voice     = 'en_US-amy-medium'  # Piper voice id (file name without .onnx)
    speed     = 1.0                 # 0.5 - 2.0, 1.0 = voice's natural pace
    sapiRate  = 1                   # -10 - 10, used by the Windows voice
    maxChars  = 2000                # longer replies are cut off; 0 = no limit
    readCode  = $false              # read fenced code blocks instead of skipping them
}
$cfgPath = Join-Path $root 'config.json'
if (Test-Path $cfgPath) {
    try {
        $user = Get-Content -Raw -Encoding UTF8 $cfgPath | ConvertFrom-Json
        foreach ($p in $user.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
    } catch {}
}

# ---- get the reply text ----
[Console]::InputEncoding = [Text.Encoding]::UTF8
$raw = [Console]::In.ReadToEnd()
try { $hook = $raw | ConvertFrom-Json } catch { exit 0 }

# The app's Speak button sets claude_tts_test so it works even while reading is off.
if (-not $cfg.enabled -and -not $hook.claude_tts_test -and -not $Print) { exit 0 }

# Prefer the field Claude Code passes directly; fall back to the transcript.
$text = $hook.last_assistant_message
if ([string]::IsNullOrWhiteSpace($text) -and $hook.transcript_path -and (Test-Path $hook.transcript_path)) {
    $lines = Get-Content -LiteralPath $hook.transcript_path -Encoding UTF8
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        try { $entry = $lines[$i] | ConvertFrom-Json } catch { continue }
        if ($entry.type -ne 'assistant') { continue }
        $parts = @($entry.message.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text })
        if ($parts.Count -gt 0) { $text = $parts -join "`n"; break }
    }
}
if ([string]::IsNullOrWhiteSpace($text)) { exit 0 }

# ---- strip markdown so it isn't read out as symbols ----
$clean = $text
if ($cfg.readCode) {
    $clean = [regex]::Replace($clean, '(?m)^\s*```.*$', ' ')
} else {
    $clean = [regex]::Replace($clean, '(?s)```.*?```', ' code block omitted. ')
}
$clean = [regex]::Replace($clean, '\[([^\]]+)\]\([^)]+\)', '$1')
$clean = [regex]::Replace($clean, 'https?://\S+', ' link ')

# File paths are read as just the file name: C:\Dev\app\setup.exe -> setup.exe, src/app.py:42 -> app.py.
$seg = '[^\s\\/:*?"<>|,;()\[\]{}`]+'
$pathToLeaf = {
    param($m)
    $path = [regex]::Replace($m.Value, '(:\d+)+$', '').TrimEnd('\', '/')
    $leaf = ($path -split '[\\/]' | Where-Object { $_ })[-1]
    # A relative path only counts when it ends in a file name, so "and/or" and dates are left alone.
    if ($m.Groups['rel'].Success -and $leaf.TrimEnd('.') -notmatch '\.[A-Za-z0-9]{1,8}$') { return $m.Value }
    return $leaf
}
# Paths with spaces are only safe to spot inside a `code span`.
$clean = [regex]::Replace($clean, '`((?:[A-Za-z]:|\\\\|~|%\w+%|\$env:\w+)[\\/][^`"<>|*?\r\n]*)`', { param($m) & $pathToLeaf $m.Groups[1] })
$absolute = '(?:[A-Za-z]:[\\/]|\\\\|(?:~|%\w+%|\$env:\w+|\.{1,2})[\\/]|/(?=' + $seg + '/))' + $seg + '(?:[\\/]' + $seg + ')*[\\/]?(?::\d+)*'
$relative = '(?<rel>)(?:' + $seg + '[\\/])+' + $seg + '(?::\d+)*'
$clean = [regex]::Replace($clean, '(?<![\w\\/.:~%$-])(?:' + $absolute + '|' + $relative + ')', $pathToLeaf)

$clean = [regex]::Replace($clean, '[`*#>|~]', '')
$clean = $clean.Replace('_', ' ')  # snake_case names read as words, not glued together
$clean = [regex]::Replace($clean, '(?m)^\s*[-:]{3,}\s*$', '')
$clean = [regex]::Replace($clean, '(?m)^\s*[-+]\s+', '')
$clean = [regex]::Replace($clean, '\s+', ' ').Trim()
$max = [int]$cfg.maxChars
if ($max -gt 0 -and $clean.Length -gt $max) { $clean = $clean.Substring(0, $max) + '. Reply truncated.' }
if ([string]::IsNullOrWhiteSpace($clean)) { exit 0 }
if ($Print) { [Console]::Out.Write($clean); exit 0 }

# Cut off any reply still being read so they don't overlap.
Stop-Speaking
Set-Content -Path $pidFile -Value $PID

# ---- speak: Piper (local neural voice), falling back to the Windows voice ----
$piper = Join-Path $root 'piper\piper.exe'
$voice = Join-Path $root ("voices\" + $cfg.voice + '.onnx')
$wav   = Join-Path $env:TEMP "claude_tts_$PID.wav"

if ($cfg.engine -eq 'piper' -and (Test-Path $piper) -and (Test-Path $voice)) {
    $speed = [double]$cfg.speed
    if ($speed -le 0) { $speed = 1.0 }
    $lengthScale = [string]::Format([Globalization.CultureInfo]::InvariantCulture, '{0:0.###}', 1.0 / $speed)
    $clean | & $piper --model $voice --length_scale $lengthScale --output_file $wav 2>$null | Out-Null
}
if (Test-Path $wav) {
    $player = New-Object System.Media.SoundPlayer $wav
    $player.PlaySync()
    $player.Dispose()
    Remove-Item $wav
} else {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.Rate = [int]$cfg.sapiRate
    $synth.Speak($clean)
}

if ((Get-Content $pidFile) -eq $PID) { Remove-Item $pidFile }
