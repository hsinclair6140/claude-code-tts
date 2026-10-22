# Claude Code TTS

Hear [Claude Code](https://claude.com/claude-code)'s replies read aloud on Windows, using natural-sounding voices that run **entirely on your machine** — no cloud TTS, no API keys, no data leaving your computer.

Pair it with Claude Code's built-in `/voice` dictation for a hands-free, two-way voice loop: talk to Claude, hear the answer.

> Community project. Not affiliated with or endorsed by Anthropic.

## How it works

- Claude Code runs a **Stop hook** every time it finishes a reply.
- The hook runs `speak.ps1`, which takes the reply, strips markdown, code blocks and URLs, shortens file paths to just the file name, and speaks it with:
  - **[Piper](https://github.com/rhasspy/piper)** — a fast, local neural text-to-speech engine with 100+ voices in many languages (default), or
  - **the built-in Windows voice** (SAPI) as a fallback.
- A new reply cuts off the one still being read, so they never overlap.
- The **Claude Code TTS** app gives you a small settings window to install everything and change the voice, speed and more.

## Install (recommended)

1. Download `ClaudeCodeTTS-Setup-<version>.exe` from the [latest release](../../releases/latest) and run it. No admin rights needed.
2. In the window that opens, click **Install / Repair**. This downloads Piper (~20 MB) and the default voice (~60 MB) and adds the hook to `~/.claude/settings.json` (a backup is saved as `settings.json.bak`).
3. Restart Claude Code (or open `/hooks` once) so it picks up the new hook.
4. Optional: type `/voice` in Claude Code to talk instead of type.

Prefer no installer? Download the portable `claude-code-tts.exe` instead and run it directly. It does the same thing.

> Windows SmartScreen may warn about an unrecognized app because the exe isn't code-signed. Click **More info → Run anyway**, or build it yourself from source (below).

## Using the app

| Setting | What it does |
| --- | --- |
| Read Claude's replies aloud | Master on/off switch. Takes effect on the next reply, no restart needed. |
| Engine | Piper (natural) or the Windows built-in voice. |
| Language / Voice | Pick any Piper voice. Voices download automatically the first time you choose them (a ✓ marks downloaded ones). |
| Speed | 0.5x – 2x for Piper. |
| Windows rate | -10 – 10 for the built-in voice. |
| Max characters | Long replies are cut off after this many characters (0 = no limit). |
| Read code blocks | Off by default: code blocks are announced as "code block omitted". |
| Stop hotkey | Press it anywhere in Windows to stop the reply being read. Default **Ctrl+Alt+S**; pick *Key only* for a single function key like **F8**, or *Off*. While set, the key does nothing else in other programs. A small background listener (`claude-code-tts.exe --listen`) starts at sign-in to catch it. You can also run `claude-code-tts.exe --stop`. |
| Test | Type something and click **Speak** to hear the current settings. **Stop** silences the current reply. |
| Remove hook | Stops Claude Code from speaking without uninstalling anything. |
| Check for updates | Looks for a newer release on GitHub. The installed app downloads and runs the new installer; the portable exe opens the download page. |

All settings except the hook take effect immediately: they're stored in `~/.claude/tts/config.json`, which the hook reads every time.

## Files it creates

```
~/.claude/settings.json      Stop hook entry added (everything else left as-is)
~/.claude/tts/
    speak.ps1                the hook script
    config.json              your settings
    piper/                   Piper engine
    voices/                  downloaded voice models
    voices.json              cached voice catalog
HKCU\...\CurrentVersion\Run  "ClaudeCodeTTSHotkey" starts the stop hotkey listener at sign-in
```

## Uninstall

Uninstall **Claude Code TTS** from *Settings → Apps*. This removes the hook from `settings.json` and deletes `~/.claude/tts`. (Using the portable exe? Run `claude-code-tts.exe --uninstall`.)

## Manual setup (no app)

If you'd rather wire it up by hand:

1. Copy [`assets/speak.ps1`](assets/speak.ps1) to `~/.claude/tts/speak.ps1`.
2. Optional, for natural voices: extract [Piper for Windows](https://github.com/rhasspy/piper/releases/tag/2023.11.14-2) into `~/.claude/tts/piper/`, and put a voice's `.onnx` and `.onnx.json` from [piper-voices](https://huggingface.co/rhasspy/piper-voices) into `~/.claude/tts/voices/`. Without Piper, the Windows voice is used.
3. Add this to `~/.claude/settings.json` (merge it with any existing `hooks`), replacing `YOU` with your user name:

   ```json
   {
     "hooks": {
       "Stop": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:/Users/YOU/.claude/tts/speak.ps1\"",
               "async": true,
               "timeout": 600
             }
           ]
         }
       ]
     }
   }
   ```

4. Optional: create `~/.claude/tts/config.json` with any of the keys below (defaults shown):

   ```json
   { "enabled": true, "engine": "piper", "voice": "en_US-amy-medium",
     "speed": 1.0, "sapiRate": 1, "maxChars": 2000, "readCode": false }
   ```

## Build from source

Requirements: Windows, Python 3.10+, and [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).

```powershell
.\build.ps1
```

This runs the tests, then produces `dist\claude-code-tts.exe` (portable) and `dist\ClaudeCodeTTS-Setup-<version>.exe` (installer). To run the app without building: `python src\claude_tts.py`.

## Troubleshooting

- **Nothing is spoken.** Make sure the app shows the hook as *Installed*, then restart Claude Code or open `/hooks`. Check that the master switch is on, and test with the **Speak** button.
- **Robotic voice.** You're on the Windows engine, or the selected Piper voice isn't downloaded yet. Choose Piper and a voice with a ✓.
- **settings.json is not valid JSON.** The app refuses to edit a settings file it can't parse, so it never damages it. Fix the file (or restore `settings.json.bak`) and click **Install / Repair** again.

## Credits and licenses

- This project: [MIT](LICENSE).
- [Piper](https://github.com/rhasspy/piper) (MIT) and its [voices](https://huggingface.co/rhasspy/piper-voices) are downloaded at install time and are not bundled here. Each voice has its own license — see its model card on Hugging Face before using it beyond personal use.
