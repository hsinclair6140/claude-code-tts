"""Claude Code TTS - settings app and installer for reading Claude Code replies aloud.

Installs a Stop hook into ~/.claude/settings.json that runs ~/.claude/tts/speak.ps1,
which speaks each reply with Piper (a local neural voice engine) or the built-in
Windows voice. Everything runs offline once the engine and a voice are downloaded.

Usage:
    claude-code-tts.exe              open the settings window
    claude-code-tts.exe --stop       stop the reply currently being read
    claude-code-tts.exe --pause      pause the reply being read, or resume it
    claude-code-tts.exe --listen     run the hotkey listener (started at sign-in)
    claude-code-tts.exe --uninstall  remove the hook and all downloaded files
"""

import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
import webbrowser
import winreg
import zipfile
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, ttk

APP_NAME = "Claude Code TTS"
APP_VERSION = "1.2.0"

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
TTS_DIR = CLAUDE_DIR / "tts"
SPEAK_SCRIPT = TTS_DIR / "speak.ps1"
CONFIG_FILE = TTS_DIR / "config.json"
PIPER_EXE = TTS_DIR / "piper" / "piper.exe"
VOICES_DIR = TTS_DIR / "voices"
VOICES_CACHE = TTS_DIR / "voices.json"

# The hotkey listener (stop and pause): a windowless background process started at sign-in.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ClaudeCodeTTSHotkey"
LISTENER_MUTEX = "Local\\ClaudeCodeTTSHotkeyListener"
LISTENER_QUIT_EVENT = "Local\\ClaudeCodeTTSHotkeyQuit"

PIPER_URL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
LATEST_RELEASE_API = "https://api.github.com/repos/hsinclair6140/claude-code-tts/releases/latest"
RELEASES_PAGE = "https://github.com/hsinclair6140/claude-code-tts/releases/latest"
# Where the installer puts the app (DefaultDirName in installer/setup.iss).
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "Claude Code TTS"

# Script paths a hook of ours may point at (current layout and the original manual setup).
OUR_SCRIPT_MARKERS = (".claude/tts/speak.ps1", ".claude/hooks/speak.ps1")

DEFAULT_CONFIG = {
    "enabled": True,
    "engine": "piper",
    "voice": "en_US-amy-medium",
    "speed": 1.0,
    "sapiRate": 1,
    "maxChars": 2000,
    "readCode": False,
    "leadInMs": 750,
    "stopHotkey": "Ctrl+Alt+S",
    "pauseHotkey": "Ctrl+Alt+P",
}
# Config key of each hotkey -> the id it's registered under (2 is taken by hotkey_available).
HOTKEYS = {"stopHotkey": 1, "pauseHotkey": 3}

HOTKEY_MODIFIERS = ["", "Ctrl", "Alt", "Shift", "Ctrl+Alt", "Ctrl+Shift", "Alt+Shift", "Ctrl+Alt+Shift"]
MODIFIER_FLAGS = {"Alt": 0x1, "Ctrl": 0x2, "Shift": 0x4}  # MOD_ALT, MOD_CONTROL, MOD_SHIFT
# F12 is reserved by Windows for debuggers. Letters and digits need Ctrl or Alt so the
# hotkey can't swallow ordinary typing.
FUNCTION_KEYS = [f"F{n}" for n in range(1, 12)]
TYPING_KEYS = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(d) for d in range(10)]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# Files and settings
# ---------------------------------------------------------------------------

def bundled_file(name):
    """Path to a file shipped with the app (inside the exe when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parent.parent / "assets" / name


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def hook_command():
    return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{SPEAK_SCRIPT.as_posix()}"'


def is_our_hook(hook):
    text = " ".join([hook.get("command", "")] + list(hook.get("args", [])))
    text = text.replace("\\", "/").lower()
    return any(marker in text for marker in OUR_SCRIPT_MARKERS)


def read_settings():
    """Load ~/.claude/settings.json. Raises ValueError if it exists but isn't valid JSON,
    so we never overwrite a file we couldn't parse."""
    if not SETTINGS_FILE.exists():
        return {}
    text = SETTINGS_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError as e:
        raise ValueError(f"{SETTINGS_FILE} is not valid JSON, so it was left untouched.\n\n{e}")


def write_settings(settings):
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    if SETTINGS_FILE.exists():
        shutil.copy2(SETTINGS_FILE, SETTINGS_FILE.with_name("settings.json.bak"))
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def strip_our_hooks(settings):
    """Remove our Stop hook entries in place. Returns True if anything was removed."""
    groups = settings.get("hooks", {}).get("Stop", [])
    removed = False
    kept_groups = []
    for group in groups:
        hooks = group.get("hooks", [])
        kept = [h for h in hooks if not is_our_hook(h)]
        removed |= len(kept) != len(hooks)
        if kept:
            kept_groups.append({**group, "hooks": kept})
    if removed:
        if kept_groups:
            settings["hooks"]["Stop"] = kept_groups
        else:
            del settings["hooks"]["Stop"]
            if not settings["hooks"]:
                del settings["hooks"]
    return removed


def install_hook():
    settings = read_settings()
    strip_our_hooks(settings)
    settings.setdefault("hooks", {}).setdefault("Stop", []).append({
        "hooks": [{
            "type": "command",
            "command": hook_command(),
            "async": True,
            "timeout": 600,
        }]
    })
    write_settings(settings)


def remove_hook():
    settings = read_settings()
    if strip_our_hooks(settings):
        write_settings(settings)


def hook_installed():
    try:
        settings = read_settings()
    except (OSError, ValueError):
        return False
    for group in settings.get("hooks", {}).get("Stop", []):
        for hook in group.get("hooks", []):
            if ".claude/tts/speak.ps1" in hook.get("command", "").replace("\\", "/").lower():
                return True
    return False


# ---------------------------------------------------------------------------
# Downloads and voices
# ---------------------------------------------------------------------------

def download(url, dest, progress=None):
    """Download url to dest, calling progress(done_bytes, total_bytes) as it goes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": f"claude-code-tts/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(256 * 1024):
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    tmp.replace(dest)


def install_piper(progress=None):
    zip_path = TTS_DIR / "piper.zip"
    download(PIPER_URL, zip_path, progress)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(TTS_DIR)  # the archive's top-level folder is "piper/"
    zip_path.unlink()


def voice_downloaded(voice_id):
    return (VOICES_DIR / f"{voice_id}.onnx").exists() and (VOICES_DIR / f"{voice_id}.onnx.json").exists()


def load_voice_catalog():
    """Return {voice_id: info} from Piper's voices.json, refreshing the local cache when online."""
    try:
        download(VOICES_BASE + "voices.json", VOICES_CACHE)
    except OSError:
        pass
    try:
        return json.loads(VOICES_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Offline with no cache: offer whatever voices are already on disk.
        return {p.stem: {} for p in VOICES_DIR.glob("*.onnx")}


def download_voice(voice_id, info, progress=None):
    files = [f for f in info.get("files", {}) if f.endswith((".onnx", ".onnx.json"))]
    sizes = {f: info["files"][f].get("size_bytes", 0) for f in files}
    total = sum(sizes.values())
    done_before = 0
    for f in sorted(files, key=lambda f: f.endswith(".onnx")):  # small .json first
        def file_progress(done, _total, base=done_before):
            if progress:
                progress(base + done, total)
        download(VOICES_BASE + f, VOICES_DIR / Path(f).name, file_progress)
        done_before += sizes[f]


def voice_language(info):
    lang = info.get("language", {})
    if not lang:
        return "Downloaded"
    return f"{lang.get('name_english', '?')} ({lang.get('country_english', '?')})"


def voice_label(voice_id, info):
    name = info.get("name", voice_id).replace("_", " ")
    quality = info.get("quality")
    label = f"{name} ({quality})" if quality else name
    return ("✓ " if voice_downloaded(voice_id) else "    ") + label


# ---------------------------------------------------------------------------
# Speaking
# ---------------------------------------------------------------------------

def run_speak(payload=None, stop=False, pause=False):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SPEAK_SCRIPT)]
    if stop:
        args.append("-Stop")
    if pause:
        args.append("-Pause")
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, creationflags=NO_WINDOW)
    proc.stdin.write(json.dumps(payload or {}).encode("utf-8"))
    proc.stdin.close()


def split_hotkey(hotkey):
    """Split "Ctrl+Alt+S" into ("Ctrl+Alt", "S") and "F8" into ("", "F8").
    Returns None when the hotkey is off or invalid."""
    modifiers, _, key = (hotkey or "").rpartition("+")
    if modifiers not in HOTKEY_MODIFIERS or key not in hotkey_keys(modifiers):
        return None
    return modifiers, key


def hotkey_keys(modifiers):
    """Keys that may be combined with modifiers."""
    if "Ctrl" in modifiers or "Alt" in modifiers:
        return TYPING_KEYS + FUNCTION_KEYS
    return FUNCTION_KEYS


def register_hotkey(hotkey, hotkey_id=1):
    """Register hotkey for the calling thread. Returns False if it's invalid or taken."""
    parts = split_hotkey(hotkey)
    if not parts:
        return False
    modifiers, key = parts
    flags = 0x4000  # MOD_NOREPEAT
    for name in filter(None, modifiers.split("+")):
        flags |= MODIFIER_FLAGS[name]
    vk = 0x70 + int(key[1:]) - 1 if key in FUNCTION_KEYS else ord(key)
    return bool(ctypes.windll.user32.RegisterHotKey(None, hotkey_id, flags, vk))


def hotkey_available(hotkey):
    """True if no other program has claimed hotkey."""
    if not register_hotkey(hotkey, hotkey_id=2):
        return False
    ctypes.windll.user32.UnregisterHotKey(None, 2)
    return True


def kernel32():
    k32 = ctypes.windll.kernel32
    for name in ("CreateMutexW", "OpenMutexW", "CreateEventW", "OpenEventW"):
        getattr(k32, name).restype = wintypes.HANDLE
    return k32


def listen_for_hotkey():
    """Run the hotkey listener until both hotkeys are turned off, the app is uninstalled,
    or stop_listener() is called. Picks up hotkey changes from config.json as they happen."""
    k32, user32 = kernel32(), ctypes.windll.user32
    mutex = k32.CreateMutexW(None, True, LISTENER_MUTEX)
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS: another listener is running
        return
    quit_event = wintypes.HANDLE(k32.CreateEventW(None, True, False, LISTENER_QUIT_EVENT))
    registered = dict.fromkeys(HOTKEYS.values())
    mtime = None
    msg = wintypes.MSG()
    while True:
        try:
            current = CONFIG_FILE.stat().st_mtime
        except OSError:
            break  # uninstalled
        if current != mtime:
            mtime = current
            config = load_config()
            if not any(split_hotkey(config[name]) for name in HOTKEYS):
                break  # turned off
            for name, hotkey_id in HOTKEYS.items():
                hotkey = config[name] if split_hotkey(config[name]) else None
                if hotkey != registered[hotkey_id]:
                    if registered[hotkey_id]:
                        user32.UnregisterHotKey(None, hotkey_id)
                    registered[hotkey_id] = hotkey if hotkey and register_hotkey(hotkey, hotkey_id) else None
        # Wake on the quit event, a hotkey press, or every second to check config.json.
        if user32.MsgWaitForMultipleObjects(1, ctypes.byref(quit_event), False, 1000, 0x4FF) == 0:
            break
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            if msg.message == 0x312 and SPEAK_SCRIPT.exists():  # WM_HOTKEY; wParam is the hotkey id
                run_speak(stop=msg.wParam == HOTKEYS["stopHotkey"], pause=msg.wParam == HOTKEYS["pauseHotkey"])
    k32.ReleaseMutex(mutex)


def listener_running():
    k32 = kernel32()
    mutex = k32.OpenMutexW(0x100000, False, LISTENER_MUTEX)  # SYNCHRONIZE
    if mutex:
        k32.CloseHandle(mutex)
    return bool(mutex)


def listener_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--listen"]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return [str(pythonw if pythonw.exists() else sys.executable), str(Path(__file__).resolve()), "--listen"]


def update_listener(config):
    """Start the listener at sign-in and now when a hotkey is set; otherwise stop starting it.
    A running listener exits by itself once config.json says the hotkeys are off."""
    wanted = any(split_hotkey(config.get(name)) for name in HOTKEYS)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if wanted:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(listener_command()))
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
    if wanted and not listener_running():
        subprocess.Popen(listener_command(), close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def stop_listener():
    """Ask a running listener to exit and wait for it, so its exe can be replaced or deleted."""
    k32 = kernel32()
    event = k32.OpenEventW(0x2, False, LISTENER_QUIT_EVENT)  # EVENT_MODIFY_STATE
    if not event:
        return
    k32.SetEvent(event)
    k32.CloseHandle(event)
    mutex = k32.OpenMutexW(0x100000, False, LISTENER_MUTEX)  # SYNCHRONIZE
    if mutex:
        if k32.WaitForSingleObject(mutex, 5000) in (0, 0x80):  # acquired or abandoned
            k32.ReleaseMutex(mutex)
        k32.CloseHandle(mutex)


def uninstall_everything():
    if SPEAK_SCRIPT.exists():
        run_speak(stop=True)
    stop_listener()
    try:
        update_listener({})
    except OSError:
        pass
    try:
        remove_hook()
    except (OSError, ValueError):
        pass
    shutil.rmtree(TTS_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------

def parse_version(text):
    """Turn "v1.2.0" into (1, 2, 0). Returns None if text isn't a version number."""
    parts = (text or "").strip().lstrip("vV").split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def update_available(latest_tag, current=APP_VERSION):
    latest = parse_version(latest_tag)
    return latest is not None and latest > parse_version(current)


def latest_release():
    """Return (tag, release page URL, installer download URL or None) for the newest GitHub release."""
    req = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": f"claude-code-tts/{APP_VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.load(resp)
    installer = next((asset["browser_download_url"] for asset in release.get("assets", [])
                      if asset["name"].startswith("ClaudeCodeTTS-Setup-") and asset["name"].endswith(".exe")),
                     None)
    return release.get("tag_name", ""), release.get("html_url", RELEASES_PAGE), installer


def running_installed_copy():
    """True for the installer's copy of the app; False for the portable exe or running from source."""
    if not getattr(sys, "frozen", False):
        return False
    return os.path.normcase(Path(sys.executable).parent) == os.path.normcase(INSTALL_DIR)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.catalog = {}
        self.events = queue.Queue()
        self.busy = False
        self.save_job = None

        root.title(f"{APP_NAME} {APP_VERSION}")
        root.iconbitmap(default=str(bundled_file("icon.ico")))
        root.resizable(False, False)
        self.build()
        self.refresh_status()
        if hook_installed():
            try:
                update_listener(self.config)
            except OSError:
                pass
        self.run_task("Loading voice list...", self.task_load_catalog)
        root.after(100, self.pump_events)

    # ---- layout ----
    def build(self):
        pad = {"padx": 10, "pady": 4}
        main = ttk.Frame(self.root, padding=12)
        main.grid(sticky="nsew")
        main.columnconfigure(0, weight=1)

        # Setup
        setup = ttk.LabelFrame(main, text="Setup", padding=8)
        setup.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text="Voice engine:").grid(row=0, column=0, sticky="w")
        self.piper_status = ttk.Label(setup)
        self.piper_status.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(setup, text="Claude Code hook:").grid(row=1, column=0, sticky="w")
        self.hook_status = ttk.Label(setup)
        self.hook_status.grid(row=1, column=1, sticky="w", padx=6)
        buttons = ttk.Frame(setup)
        buttons.grid(row=0, column=2, rowspan=2, sticky="e")
        self.install_btn = ttk.Button(buttons, text="Install / Repair", command=self.on_install)
        self.install_btn.pack(fill="x", pady=1)
        self.remove_btn = ttk.Button(buttons, text="Remove hook", command=self.on_remove_hook)
        self.remove_btn.pack(fill="x", pady=1)
        self.update_btn = ttk.Button(buttons, text="Check for updates", command=self.on_check_updates)
        self.update_btn.pack(fill="x", pady=1)

        # Voice
        voice = ttk.LabelFrame(main, text="Voice", padding=8)
        voice.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        voice.columnconfigure(1, weight=1)

        self.enabled_var = tk.BooleanVar(value=self.config["enabled"])
        ttk.Checkbutton(voice, text="Read Claude's replies aloud", variable=self.enabled_var,
                        command=self.on_change).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Label(voice, text="Engine:").grid(row=1, column=0, sticky="w", **pad)
        engines = ttk.Frame(voice)
        engines.grid(row=1, column=1, columnspan=2, sticky="w")
        self.engine_var = tk.StringVar(value=self.config["engine"])
        ttk.Radiobutton(engines, text="Piper (natural, offline)", value="piper",
                        variable=self.engine_var, command=self.on_change).pack(side="left")
        ttk.Radiobutton(engines, text="Windows built-in", value="sapi",
                        variable=self.engine_var, command=self.on_change).pack(side="left", padx=(12, 0))

        ttk.Label(voice, text="Language:").grid(row=2, column=0, sticky="w", **pad)
        self.lang_var = tk.StringVar()
        self.lang_box = ttk.Combobox(voice, textvariable=self.lang_var, state="readonly", width=40)
        self.lang_box.grid(row=2, column=1, columnspan=2, sticky="ew")
        self.lang_box.bind("<<ComboboxSelected>>", lambda e: self.fill_voices())

        ttk.Label(voice, text="Voice:").grid(row=3, column=0, sticky="w", **pad)
        self.voice_var = tk.StringVar()
        self.voice_box = ttk.Combobox(voice, textvariable=self.voice_var, state="readonly", width=40)
        self.voice_box.grid(row=3, column=1, columnspan=2, sticky="ew")
        self.voice_box.bind("<<ComboboxSelected>>", lambda e: self.on_voice_selected())
        self.voice_ids = []

        ttk.Label(voice, text="Speed:").grid(row=4, column=0, sticky="w", **pad)
        self.speed_var = tk.DoubleVar(value=self.config["speed"])
        self.speed_scale = ttk.Scale(voice, from_=0.5, to=2.0, variable=self.speed_var,
                                     command=lambda v: self.on_slider())
        self.speed_scale.grid(row=4, column=1, sticky="ew")
        self.speed_label = ttk.Label(voice, width=6)
        self.speed_label.grid(row=4, column=2, sticky="w", padx=6)

        ttk.Label(voice, text="Windows rate:").grid(row=5, column=0, sticky="w", **pad)
        self.rate_var = tk.IntVar(value=self.config["sapiRate"])
        self.rate_scale = ttk.Scale(voice, from_=-10, to=10, variable=self.rate_var,
                                    command=lambda v: self.on_slider())
        self.rate_scale.grid(row=5, column=1, sticky="ew")
        self.rate_label = ttk.Label(voice, width=6)
        self.rate_label.grid(row=5, column=2, sticky="w", padx=6)

        # Reading
        reading = ttk.LabelFrame(main, text="Reading", padding=8)
        reading.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(reading, text="Max characters (0 = no limit):").grid(row=0, column=0, sticky="w")
        self.max_var = tk.StringVar(value=str(self.config["maxChars"]))
        max_box = ttk.Spinbox(reading, from_=0, to=100000, increment=500, textvariable=self.max_var, width=8,
                              command=self.on_change)
        max_box.grid(row=0, column=1, sticky="w", padx=6)
        max_box.bind("<FocusOut>", lambda e: self.on_change())
        self.code_var = tk.BooleanVar(value=self.config["readCode"])
        ttk.Checkbutton(reading, text="Read code blocks (otherwise they are skipped)", variable=self.code_var,
                        command=self.on_change).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        # Each hotkey picker is (modifiers var, key var, key combobox), keyed by its config name.
        self.hotkey_pickers = {}
        for row, (name, label) in enumerate([("stopHotkey", "Stop hotkey:"), ("pauseHotkey", "Pause hotkey:")], 2):
            ttk.Label(reading, text=label).grid(row=row, column=0, sticky="w", pady=(6, 0))
            hotkey = ttk.Frame(reading)
            hotkey.grid(row=row, column=1, sticky="w", padx=6, pady=(6, 0))
            mod_var, key_var = tk.StringVar(), tk.StringVar()
            mod_box = ttk.Combobox(hotkey, textvariable=mod_var, state="readonly", width=14,
                                   values=["Off", "Key only"] + HOTKEY_MODIFIERS[1:])
            mod_box.pack(side="left")
            ttk.Label(hotkey, text="+").pack(side="left", padx=4)
            key_box = ttk.Combobox(hotkey, textvariable=key_var, state="readonly", width=4)
            key_box.pack(side="left")
            for box in (mod_box, key_box):
                box.bind("<<ComboboxSelected>>", lambda e, name=name: self.on_hotkey_change(name))
            self.hotkey_pickers[name] = (mod_var, key_var, key_box)
            self.show_hotkey(name, self.config[name])

        # Test
        test = ttk.LabelFrame(main, text="Test", padding=8)
        test.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        test.columnconfigure(0, weight=1)
        self.test_var = tk.StringVar(value="Hi! This is how Claude's replies will sound.")
        ttk.Entry(test, textvariable=self.test_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(test, text="Speak", command=self.on_test).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(test, text="Pause", command=self.on_pause).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(test, text="Stop", command=self.on_stop).grid(row=0, column=3, padx=(6, 0))

        # Status bar
        status = ttk.Frame(main)
        status.grid(row=4, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status, length=140, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="e")

        self.update_slider_labels()

    # ---- state ----
    def refresh_status(self):
        piper_ok = PIPER_EXE.exists()
        voice_ok = voice_downloaded(self.config["voice"])
        if piper_ok and voice_ok:
            self.piper_status.config(text="Piper installed", foreground="green")
        elif piper_ok:
            self.piper_status.config(text="Piper installed, selected voice not downloaded", foreground="#b36b00")
        else:
            self.piper_status.config(text="Not installed", foreground="#b00020")
        if hook_installed():
            self.hook_status.config(text="Installed - restart Claude Code or open /hooks after changes",
                                    foreground="green")
        else:
            self.hook_status.config(text="Not installed", foreground="#b00020")
        if not CLAUDE_DIR.exists():
            self.status_var.set("Claude Code not found (no ~/.claude folder). Install Claude Code first.")

    def update_slider_labels(self):
        self.speed_label.config(text=f"{self.speed_var.get():.2f}x")
        self.rate_label.config(text=f"{round(self.rate_var.get()):+d}")
        piper = self.engine_var.get() == "piper"
        for w in (self.lang_box, self.voice_box):
            w.config(state="readonly" if piper else "disabled")
        self.speed_scale.state(["!disabled"] if piper else ["disabled"])
        self.rate_scale.state(["disabled"] if piper else ["!disabled"])

    def on_slider(self):
        self.update_slider_labels()
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(300, self.on_change)

    def on_change(self):
        self.save_job = None
        try:
            max_chars = max(0, int(self.max_var.get()))
        except ValueError:
            max_chars = self.config["maxChars"]
            self.max_var.set(str(max_chars))
        self.config.update({
            "enabled": self.enabled_var.get(),
            "engine": self.engine_var.get(),
            "speed": round(self.speed_var.get(), 2),
            "sapiRate": round(self.rate_var.get()),
            "maxChars": max_chars,
            "readCode": self.code_var.get(),
        })
        save_config(self.config)
        self.update_slider_labels()
        self.status_var.set("Settings saved.")

    def show_hotkey(self, name, hotkey):
        mod_var, key_var, key_box = self.hotkey_pickers[name]
        parts = split_hotkey(hotkey)
        modifiers, key = parts or ("Off", "")
        mod_var.set(modifiers or "Key only")
        key_box.config(values=hotkey_keys(modifiers) if parts else [], state="readonly" if parts else "disabled")
        key_var.set(key)

    def selected_hotkey(self, name):
        mod_var, key_var, _ = self.hotkey_pickers[name]
        modifiers = mod_var.get()
        if modifiers == "Off":
            return ""
        modifiers = "" if modifiers == "Key only" else modifiers
        keys = hotkey_keys(modifiers)
        key = key_var.get()
        default = "F8" if name == "stopHotkey" else "F7"
        key = key if key in keys else default if default in keys else keys[0]
        return f"{modifiers}+{key}" if modifiers else key

    def on_hotkey_change(self, name):
        old, hotkey = self.config[name], self.selected_hotkey(name)
        taken = hotkey and hotkey != old and any(self.config[n] == hotkey for n in HOTKEYS if n != name)
        if taken or (hotkey and hotkey != old and not hotkey_available(hotkey)):
            self.show_hotkey(name, old)
            owner = "the other hotkey" if taken else "used by another program"
            messagebox.showwarning(APP_NAME, f"{hotkey} is already {owner}. Pick a different key.")
            return
        self.show_hotkey(name, hotkey)
        self.config[name] = hotkey
        save_config(self.config)
        try:
            update_listener(self.config)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't start the hotkey listener.\n\n{e}")
            return
        action = "stop" if name == "stopHotkey" else "pause"
        self.status_var.set(f"Press {hotkey} anywhere to {action} reading." if hotkey
                            else f"{action.capitalize()} hotkey turned off.")

    # ---- voice list ----
    def fill_languages(self):
        langs = sorted({voice_language(info) for info in self.catalog.values()})
        self.lang_box.config(values=langs)
        current = self.catalog.get(self.config["voice"])
        self.lang_var.set(voice_language(current) if current is not None else (langs[0] if langs else ""))
        self.fill_voices()

    def fill_voices(self):
        lang = self.lang_var.get()
        self.voice_ids = sorted(v for v, info in self.catalog.items() if voice_language(info) == lang)
        self.voice_box.config(values=[voice_label(v, self.catalog[v]) for v in self.voice_ids])
        if self.config["voice"] in self.voice_ids:
            self.voice_box.current(self.voice_ids.index(self.config["voice"]))
        else:
            self.voice_var.set("")

    def on_voice_selected(self):
        voice_id = self.voice_ids[self.voice_box.current()]
        if voice_downloaded(voice_id):
            self.set_voice(voice_id)
        else:
            self.run_task(f"Downloading voice {voice_id}...", self.task_download_voice, voice_id)

    def set_voice(self, voice_id):
        self.config["voice"] = voice_id
        save_config(self.config)
        self.fill_voices()
        self.refresh_status()
        self.status_var.set(f"Voice set to {voice_id}.")

    # ---- buttons ----
    def on_install(self):
        self.run_task("Installing...", self.task_install)

    def on_remove_hook(self):
        try:
            remove_hook()
        except (OSError, ValueError) as e:
            messagebox.showerror(APP_NAME, str(e))
        self.refresh_status()
        self.status_var.set("Hook removed. Claude Code will stop speaking replies.")

    def on_test(self):
        if not SPEAK_SCRIPT.exists():
            messagebox.showinfo(APP_NAME, "Click Install / Repair first.")
            return
        self.on_change()
        run_speak({"last_assistant_message": self.test_var.get(), "claude_tts_test": True})
        self.status_var.set("Speaking...")

    def on_pause(self):
        if SPEAK_SCRIPT.exists():
            run_speak(pause=True)

    def on_stop(self):
        if SPEAK_SCRIPT.exists():
            run_speak(stop=True)

    def on_check_updates(self):
        self.run_task("Checking for updates...", self.task_check_updates)

    def offer_update(self, tag, page, installer):
        version = tag.lstrip("vV")
        intro = f"Version {version} is available (you have {APP_VERSION})."
        if installer and running_installed_copy():
            if messagebox.askyesno(APP_NAME, f"{intro}\n\nDownload and install it now? "
                                             "This window closes while the installer runs."):
                self.run_task(f"Downloading version {version}...", self.task_install_update, installer)
        elif messagebox.askyesno(APP_NAME, f"{intro}\n\nOpen the download page?"):
            webbrowser.open(page)

    # ---- background tasks ----
    def run_task(self, message, fn, *args):
        if self.busy:
            return
        self.busy = True
        for button in (self.install_btn, self.update_btn):
            button.state(["disabled"])
        self.status_var.set(message)
        self.progress.config(value=0)

        def worker():
            then = None
            try:
                then = fn(*args)  # a task may return a callback to run on the UI thread once it's done
            except Exception as e:  # report any failure in the UI instead of dying silently
                self.events.put(("error", str(e)))
            self.events.put(("done", then))
        threading.Thread(target=worker, daemon=True).start()

    def report_progress(self, label):
        def progress(done, total):
            self.events.put(("progress", (label, done, total)))
        return progress

    def pump_events(self):
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                label, done, total = data
                pct = done * 100 / total if total else 0
                self.progress.config(value=pct)
                self.status_var.set(f"{label} {done / 1e6:.0f} / {total / 1e6:.0f} MB")
            elif kind == "status":
                self.status_var.set(data)
            elif kind == "call":
                data()
            elif kind == "error":
                messagebox.showerror(APP_NAME, data)
                self.status_var.set("Something went wrong.")
            elif kind == "done":
                self.busy = False
                for button in (self.install_btn, self.update_btn):
                    button.state(["!disabled"])
                self.progress.config(value=0)
                self.refresh_status()
                if data:
                    data()
        self.root.after(100, self.pump_events)

    def task_check_updates(self):
        try:
            tag, page, installer = latest_release()
        except (OSError, ValueError):
            self.events.put(("status", "Couldn't reach GitHub to check for updates."))
            return None
        if not update_available(tag):
            self.events.put(("status", f"You have the latest version ({APP_VERSION})."))
            return None
        return lambda: self.offer_update(tag, page, installer)

    def task_install_update(self, url):
        setup = Path(tempfile.gettempdir()) / url.rsplit("/", 1)[-1]
        download(url, setup, self.report_progress("Downloading update:"))
        subprocess.Popen([str(setup)])
        # Quit so the installer can replace the exe; it stops the hotkey listener itself.
        self.events.put(("call", self.root.quit))

    def task_load_catalog(self):
        catalog = load_voice_catalog()

        def apply():
            self.catalog = catalog
            self.fill_languages()
            self.status_var.set(f"{len(catalog)} voices available.")
        self.events.put(("call", apply))

    def task_download_voice(self, voice_id):
        download_voice(voice_id, self.catalog[voice_id], self.report_progress("Downloading voice:"))
        self.events.put(("call", lambda: self.set_voice(voice_id)))

    def task_install(self):
        TTS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled_file("speak.ps1"), SPEAK_SCRIPT)
        if not PIPER_EXE.exists():
            install_piper(self.report_progress("Downloading Piper:"))
        voice_id = self.config["voice"]
        if not voice_downloaded(voice_id):
            info = self.catalog.get(voice_id) or load_voice_catalog().get(voice_id)
            if not info:
                raise RuntimeError(f"Voice {voice_id} not found in the voice list.")
            download_voice(voice_id, info, self.report_progress("Downloading voice:"))
        save_config(self.config)
        install_hook()
        update_listener(self.config)
        self.events.put(("call", self.fill_voices))
        self.events.put(("status", "Installed. Restart Claude Code (or open /hooks) to start hearing replies."))


def main():
    if "--listen" in sys.argv:
        listen_for_hotkey()
        return
    if "--quit-listener" in sys.argv:
        stop_listener()
        return
    if "--stop" in sys.argv:
        if SPEAK_SCRIPT.exists():
            run_speak(stop=True)
        return
    if "--pause" in sys.argv:
        if SPEAK_SCRIPT.exists():
            run_speak(pause=True)
        return
    if "--uninstall" in sys.argv:
        uninstall_everything()
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
