# Claude Code TTS - Stop hook that reads Claude's last reply aloud, fully offline.
# Installed to ~/.claude/tts/speak.ps1 by the Claude Code TTS app. Settings live in
# config.json next to this script; edit them with the app rather than by hand.
#
#   speak.ps1            read a Stop-hook JSON payload from stdin and speak it
#   speak.ps1 -Stop      stop whatever is currently being read
#   speak.ps1 -Pause     pause the reply being read, or resume it if it's paused
#   speak.ps1 -Print     print the text that would be spoken instead of speaking it
param([switch]$Stop, [switch]$Pause, [switch]$Print)

$ErrorActionPreference = 'SilentlyContinue'
$root    = $PSScriptRoot
$pidFile = Join-Path $env:TEMP 'claude_tts.pid'
$pauseFile = Join-Path $env:TEMP 'claude_tts.paused'  # exists while the speaking process should be paused

function Stop-Speaking {
    if (Test-Path $pidFile) {
        $old = Get-Content $pidFile
        if ($old -and $old -ne $PID) {
            Stop-Process -Id $old -Force
            Wait-Process -Id $old -Timeout 2
            Remove-Item (Join-Path $env:TEMP "claude_tts_$old.wav")
        }
        Remove-Item $pidFile
    }
    Remove-Item $pauseFile
}

if ($Stop) { Stop-Speaking; exit 0 }
if ($Pause) {
    $speaking = (Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile))
    if ((Test-Path $pauseFile) -or -not $speaking) { Remove-Item $pauseFile }
    else { New-Item -ItemType File $pauseFile | Out-Null }
    exit 0
}

# ---- settings (defaults are used for anything missing from config.json) ----
$cfg = [ordered]@{
    enabled   = $true
    engine    = 'piper'             # 'piper' or 'sapi'
    voice     = 'en_US-amy-medium'  # Piper voice id (file name without .onnx)
    speed     = 1.0                 # 0.5 - 2.0, 1.0 = voice's natural pace
    sapiRate  = 1                   # -10 - 10, used by the Windows voice
    maxChars  = 2000                # longer replies are cut off; 0 = no limit
    readCode  = $false              # read fenced code blocks instead of skipping them
    leadInMs  = 750                 # silence before speech, so a waking audio device doesn't clip the first words
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
# Pause at the end of each line, so a heading or list item doesn't run into the next line.
$clean = [regex]::Replace($clean, '(?m)([^\s.!?,;:])[ \t]*\r?$', '$1.')
# Colons are a pause, never the word "colon": "Changes:" ends a sentence, "Note: x" -> "Note, x", 10:30 -> 10 30.
$clean = [regex]::Replace($clean, '(?m):[ \t]*\r?$', '.')
$clean = [regex]::Replace($clean, ':\s+', ', ')
$clean = $clean.Replace(':', ' ')
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
if (-not (Test-Path $wav)) {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.Rate = [int]$cfg.sapiRate
    $synth.SetOutputToWaveFile($wav)
    $synth.Speak($clean)
    $synth.Dispose()
}

# Put silence in front of the speech. Audio devices (Bluetooth, USB, HDMI) take a moment to wake up
# and drop whatever plays first.
function Add-LeadIn($path, $ms) {
    $bytes = [IO.File]::ReadAllBytes($path)
    $pos = 12
    $byteRate = $blockAlign = 0
    while ($pos + 8 -le $bytes.Length) {
        $id   = [Text.Encoding]::ASCII.GetString($bytes, $pos, 4)
        $size = [BitConverter]::ToInt32($bytes, $pos + 4)
        if ($id -eq 'fmt ') {
            $byteRate   = [BitConverter]::ToInt32($bytes, $pos + 16)
            $blockAlign = [BitConverter]::ToInt16($bytes, $pos + 20)
        } elseif ($id -eq 'data') {
            if ($blockAlign -le 0) { return }
            $pad = [int]($byteRate * $ms / 1000)
            $pad -= $pad % $blockAlign
            $out = New-Object byte[] ($bytes.Length + $pad)
            [Array]::Copy($bytes, $out, $pos + 8)
            [Array]::Copy($bytes, $pos + 8, $out, $pos + 8 + $pad, $bytes.Length - $pos - 8)
            [BitConverter]::GetBytes([int]($size + $pad)).CopyTo($out, $pos + 4)
            [BitConverter]::GetBytes([int]($out.Length - 8)).CopyTo($out, 4)
            [IO.File]::WriteAllBytes($path, $out)
            return
        }
        $pos += 8 + $size + ($size % 2)
    }
}
if ([int]$cfg.leadInMs -gt 0) { Add-LeadIn $wav ([int]$cfg.leadInMs) }

# MediaPlayer rather than SoundPlayer because it can pause. speak.ps1 -Pause toggles $pauseFile.
Add-Type -AssemblyName PresentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open([Uri]$wav)
$wait = [Diagnostics.Stopwatch]::StartNew()
while (-not $player.NaturalDuration.HasTimeSpan -and $wait.ElapsedMilliseconds -lt 5000) { Start-Sleep -Milliseconds 20 }
if ($player.NaturalDuration.HasTimeSpan) {
    $length  = $player.NaturalDuration.TimeSpan
    $played  = New-Object Diagnostics.Stopwatch  # playing time, so a stuck player can't keep this running
    $playing = $false
    while ($player.Position -lt $length -and $played.Elapsed -lt $length.Add([TimeSpan]::FromSeconds(5))) {
        $paused = Test-Path $pauseFile
        if ($paused -and $playing) { $player.Pause(); $played.Stop(); $playing = $false }
        elseif (-not $paused -and -not $playing) { $player.Play(); $played.Start(); $playing = $true }
        Start-Sleep -Milliseconds 50
    }
    $player.Close()
} else {
    $player.Close()
    $fallback = New-Object System.Media.SoundPlayer $wav
    $fallback.PlaySync()
    $fallback.Dispose()
}
Remove-Item $wav

if ((Get-Content $pidFile) -eq $PID) { Remove-Item $pidFile; Remove-Item $pauseFile }
