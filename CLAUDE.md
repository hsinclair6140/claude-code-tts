# Claude Code TTS

Windows-only tool that reads Claude Code's replies aloud, offline. A Claude Code **Stop hook** runs a
PowerShell script that cleans up the reply text and speaks it with Piper (local neural TTS) or the
built-in Windows voice (SAPI). A small Tk settings app installs everything and edits the settings.
User-facing docs are in `README.md`.

## Layout

| Path | What it is |
| --- | --- |
| `src/claude_tts.py` | The whole app, one file: settings UI, installer logic, hook editing, voice downloads, stop hotkey listener, update check, command-line options. |
| `assets/speak.ps1` | The hook script that actually speaks. Bundled into the exe and copied to `~/.claude/tts/` by **Install / Repair**. |
| `assets/icon.ico` | App icon (exe, window, installer). Multi-size; 16 and 24 px use a simpler design. |
| `installer/setup.iss` | Inno Setup script for the installer. |
| `build.ps1` | Runs the tests, builds `dist/claude-code-tts.exe` (PyInstaller, one-file, windowed), then the installer. |
| `tests/test_hook_settings.py` | unittest suite: settings.json merging, hotkey parsing, version comparison, and what `speak.ps1` would say. |

## How it fits together at runtime

- **Hook:** the app adds a Stop hook to `~/.claude/settings.json` that runs
  `powershell ... -File ~/.claude/tts/speak.ps1` (async). It never edits a settings.json it can't parse,
  and backs it up to `settings.json.bak` before writing. Our hook entries are recognized by
  `OUR_SCRIPT_MARKERS`, so other hooks are left untouched.
- **speak.ps1** reads the hook JSON from stdin (`last_assistant_message`, falling back to the
  transcript), strips markdown, code blocks, URLs and file paths, then speaks. Only one reply plays
  at a time: the PID of the speaking process is in `%TEMP%\claude_tts.pid`, and a new reply or
  `speak.ps1 -Stop` kills it. Speech is always rendered to a WAV (SAPI too), padded with `leadInMs` of
  leading silence so waking audio devices don't clip the first words, and played with WPF
  `MediaPlayer` so it can pause. `speak.ps1 -Pause` toggles `%TEMP%\claude_tts.paused`, which the
  playback loop polls.
- **Settings** live in `~/.claude/tts/config.json`. The script re-reads them on every reply, so changes
  take effect without a restart. Defaults are duplicated: `DEFAULT_CONFIG` in `claude_tts.py` and `$cfg` in
  `speak.ps1`. Keep the speech-related keys in sync (`stopHotkey` and `pauseHotkey` are app-only).
- **Hotkey listener:** `claude-code-tts.exe --listen` is a windowless process that calls
  `RegisterHotKey` for the stop and pause hotkeys (ids in `HOTKEYS`) and runs `speak.ps1 -Stop` or
  `-Pause`. It starts at sign-in from the
  `HKCU\...\CurrentVersion\Run` value `ClaudeCodeTTSHotkey`, polls config.json's mtime every second to
  pick up a new key, and exits when both hotkeys are off or config.json is gone. A named mutex keeps it
  single-instance; a named event (`--quit-listener` / `stop_listener()`) asks it to exit. The installer
  calls `--quit-listener` before replacing the exe. Windows shortcut hotkeys were tried first and
  dropped: they can't be a bare key like F8 and lag about a second.
- **Update check:** queries the GitHub API for the latest release of `hsinclair6140/claude-code-tts`
  and compares it with `APP_VERSION`. The installed copy downloads the asset named
  `ClaudeCodeTTS-Setup-*.exe`, runs it, and quits. The portable exe opens the release page instead.
- **Command-line options:** `--stop`, `--pause`, `--listen`, `--quit-listener`, `--uninstall`. The installer's
  uninstaller runs `--uninstall`, which also stops the listener and removes the Run value.

## Files on the user's machine

```
~/.claude/settings.json        Stop hook entry
~/.claude/tts/                 speak.ps1, config.json, piper/, voices/, voices.json
%LOCALAPPDATA%\Programs\Claude Code TTS\   installed exe (installer, no admin)
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ClaudeCodeTTSHotkey   listener at sign-in
```

## Build and test

```powershell
python -m unittest discover tests   # quick; some tests run powershell, so Windows only
.\build.ps1                         # tests + exe + installer into dist\
python src\claude_tts.py            # run the app from source
```

- To see what would be spoken without hearing it, pipe hook JSON into `speak.ps1 -Print`:
  `'{"last_assistant_message":"..."}' | powershell -NoProfile -ExecutionPolicy Bypass -File assets\speak.ps1 -Print`.
- After editing `assets/speak.ps1`, the installed copy in `~/.claude/tts/` is still the old one until
  **Install / Repair** runs (or you copy it there).
- Changes to the text cleanup in `speak.ps1` need cases in `SpokenTextTest`, including text that must
  NOT change (slash commands, "and/or", dates).

## Gotchas

- **Don't redirect build.ps1's stderr** (`2>&1`, `*>`) in Windows PowerShell 5.1. The unittest output on
  stderr becomes an error record and `$ErrorActionPreference = 'Stop'` aborts the build. Run it plainly.
- **Inno Setup "EndUpdateResource failed (110)":** a half-written `dist\ClaudeCodeTTS-Setup-*.exe`
  from a failed run blocks the next one. Delete it and rebuild.
- **Stale icon after an upgrade** is Explorer's icon cache, not the build. `ChangesAssociations=yes` in
  setup.iss refreshes it on install; `ie4uinit.exe -show` does it by hand.
- **Paths in speak.ps1:** relative paths only count when the last part has a file extension; that is
  what keeps "and/or" and dates intact. Paths with spaces are only caught inside backticks.

## Releasing

1. Bump `APP_VERSION` in `src/claude_tts.py`. build.ps1 reads it and passes it to the installer, and the
   update check compares against it.
2. `.\build.ps1`, then commit and tag `vX.Y.Z` (the tag must parse as a version for the update check).
3. Create a GitHub release from the tag and attach both `dist\ClaudeCodeTTS-Setup-X.Y.Z.exe` and
   `dist\claude-code-tts.exe`. Keep the installer's name: the update check finds it by that prefix.
