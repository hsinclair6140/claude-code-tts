"""Tests for the settings.json hook merge logic. Run: python -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import claude_tts  # noqa: E402


class HookSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        claude_tts.CLAUDE_DIR = home / ".claude"
        claude_tts.SETTINGS_FILE = claude_tts.CLAUDE_DIR / "settings.json"
        claude_tts.TTS_DIR = claude_tts.CLAUDE_DIR / "tts"
        claude_tts.SPEAK_SCRIPT = claude_tts.TTS_DIR / "speak.ps1"
        claude_tts.CLAUDE_DIR.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        claude_tts.SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        return json.loads(claude_tts.SETTINGS_FILE.read_text(encoding="utf-8"))

    def test_install_into_missing_file(self):
        claude_tts.SETTINGS_FILE.unlink(missing_ok=True)
        claude_tts.install_hook()
        self.assertTrue(claude_tts.hook_installed())
        self.assertEqual(len(self.read()["hooks"]["Stop"]), 1)

    def test_install_preserves_other_settings_and_hooks(self):
        other = {"type": "command", "command": "echo done"}
        self.write({"model": "opus", "hooks": {"Stop": [{"hooks": [other]}], "PreToolUse": [{"hooks": [other]}]}})
        claude_tts.install_hook()
        s = self.read()
        self.assertEqual(s["model"], "opus")
        self.assertIn("PreToolUse", s["hooks"])
        self.assertEqual(s["hooks"]["Stop"][0]["hooks"], [other])
        self.assertEqual(len(s["hooks"]["Stop"]), 2)
        self.assertTrue(claude_tts.SETTINGS_FILE.with_name("settings.json.bak").exists())

    def test_install_twice_does_not_duplicate(self):
        claude_tts.install_hook()
        claude_tts.install_hook()
        self.assertEqual(len(self.read()["hooks"]["Stop"]), 1)

    def test_install_replaces_legacy_manual_hook(self):
        legacy = {"type": "command", "command": "powershell",
                  "args": ["-File", "C:\\Users\\x\\.claude\\hooks\\speak.ps1"], "async": True}
        self.write({"hooks": {"Stop": [{"hooks": [legacy]}]}})
        claude_tts.install_hook()
        stop = self.read()["hooks"]["Stop"]
        self.assertEqual(len(stop), 1)
        self.assertIn(".claude/tts/speak.ps1", stop[0]["hooks"][0]["command"])

    def test_remove_leaves_other_hooks(self):
        other = {"type": "command", "command": "echo done"}
        self.write({"hooks": {"Stop": [{"hooks": [other]}]}})
        claude_tts.install_hook()
        claude_tts.remove_hook()
        self.assertEqual(self.read(), {"hooks": {"Stop": [{"hooks": [other]}]}})
        self.assertFalse(claude_tts.hook_installed())

    def test_remove_cleans_up_empty_hooks_section(self):
        self.write({"model": "opus"})
        claude_tts.install_hook()
        claude_tts.remove_hook()
        self.assertEqual(self.read(), {"model": "opus"})

    def test_invalid_json_is_never_overwritten(self):
        claude_tts.SETTINGS_FILE.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            claude_tts.install_hook()
        self.assertEqual(claude_tts.SETTINGS_FILE.read_text(encoding="utf-8"), "{ not json")


if __name__ == "__main__":
    unittest.main()
