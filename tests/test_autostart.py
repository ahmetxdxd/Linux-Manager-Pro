"""Tests for lmp.core.autostart."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lmp.core import autostart
from lmp.core.util import data_dir


class AutostartTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(autostart.user_dir(), ignore_errors=True)   # every test starts clean
        (data_dir() / "autostart-disabled.json").unlink(missing_ok=True)
        self.sys_dir = Path(tempfile.mkdtemp()) / "autostart"
        self.sys_dir.mkdir()
        os.environ["XDG_CONFIG_DIRS"] = str(self.sys_dir.parent)
        (self.sys_dir / "sys.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=System Tool\nExec=sysapp\n")

    def tearDown(self):
        os.environ.pop("XDG_CONFIG_DIRS", None)

    def test_disable_and_enable_system_entry(self):
        item = next(i for i in autostart.list_items() if i.desktop_id == "sys.desktop")
        self.assertTrue(item.enabled)
        autostart.set_enabled(item, False)
        override = autostart.user_dir() / "sys.desktop"
        self.assertIn("Hidden=true", override.read_text())
        self.assertIn("Exec=sysapp", (self.sys_dir / "sys.desktop").read_text())
        item = next(i for i in autostart.list_items() if i.desktop_id == "sys.desktop")
        self.assertFalse(item.enabled)
        autostart.set_enabled(item, True)
        self.assertFalse(override.exists(), "a user copy identical to the system entry must be removed")

    def test_disable_all_and_undo(self):
        mine = autostart.add_item("Mine", "mine-app")
        autostart.add_item("Already off", "off-app")
        off = next(i for i in autostart.list_items() if i.name == "Already off")
        autostart.set_enabled(off, False)

        changed = autostart.disable_all(autostart.list_items())
        self.assertCountEqual(changed, ["sys.desktop", mine.name])
        self.assertFalse(any(i.enabled for i in autostart.list_items()))
        self.assertCountEqual(autostart.disabled_snapshot(), changed)

        self.assertEqual(autostart.restore_disabled(), 2)
        enabled = {i.name for i in autostart.list_items() if i.enabled}
        self.assertEqual(enabled, {"System Tool", "Mine"})   # "Already off" stays off
        self.assertEqual(autostart.disabled_snapshot(), [])

    def test_add_item(self):
        path = autostart.add_item("My Script", "/home/x/script.sh", delay=5)
        text = path.read_text()
        self.assertIn("sleep 5", text)
        self.assertIn("Name=My Script", text)

    def test_blame(self):
        rows = autostart.parse_blame("1min 2.500s foo.service\n  845ms bar.service\n")
        self.assertAlmostEqual(rows[0][1], 62.5)
        self.assertEqual(rows[1][0], "bar.service")


class StartupSourcesTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(autostart.user_dir(), ignore_errors=True)
        autostart._blocked_path().unlink(missing_ok=True)

    def test_app_that_rewrites_its_file_is_kept_off(self):
        autostart.add_item("Chat", "chat-app")
        item = next(i for i in autostart.list_items() if i.name == "Chat")
        autostart.set_enabled(item, False)
        self.assertIn(item.desktop_id, autostart.blocked_ids())
        # the app rewrites its autostart file on launch, without Hidden=true
        (autostart.user_dir() / item.desktop_id).write_text(
            "[Desktop Entry]\nType=Application\nName=Chat\nExec=chat-app\n")
        autostart.enforce_blocked()
        item = next(i for i in autostart.list_items() if i.name == "Chat")
        self.assertFalse(item.enabled)
        autostart.set_enabled(item, True)          # switching it on again is respected
        self.assertNotIn(item.desktop_id, autostart.blocked_ids())
        autostart.enforce_blocked()
        self.assertTrue(next(i for i in autostart.list_items() if i.name == "Chat").enabled)

    def test_hyprland_exec_once(self):
        hypr = Path(os.environ["HOME"]) / ".config/hypr"
        hypr.mkdir(parents=True, exist_ok=True)
        conf = hypr / "hyprland.conf"
        conf.write_text("monitor=,preferred,auto,1\nexec-once = waybar\n"
                        "exec-once = [workspace 2 silent] discord --start-minimized\n"
                        "# exec-once = old-thing\n")
        os.environ["XDG_CURRENT_DESKTOP"] = "Hyprland"
        try:
            items = [i for i in autostart.list_items() if i.kind == autostart.WM]
            self.assertEqual([i.name for i in items], ["discord", "waybar"])
            discord = items[0]
            self.assertFalse(discord.desktop_mismatch)
            autostart.set_enabled(discord, False)
            self.assertIn("#lmp-off# exec-once = [workspace 2 silent] discord", conf.read_text())
            items = {i.name: i for i in autostart.list_items() if i.kind == autostart.WM}
            self.assertFalse(items["discord"].enabled)
            self.assertTrue(items["waybar"].enabled)
            autostart.set_enabled(items["discord"], True)
            self.assertIn("\nexec-once = [workspace 2 silent] discord", conf.read_text())
            self.assertTrue((hypr / "hyprland.conf.lmp-backup").exists())
            # "Disable all" must not switch off the bar or other WM commands
            autostart.disable_all(autostart.list_items())
            self.assertIn("\nexec-once = waybar", conf.read_text())
            self.assertFalse(autostart.xdg_entries_run_here())
        finally:
            os.environ.pop("XDG_CURRENT_DESKTOP", None)
            shutil.rmtree(hypr)
            (data_dir() / "autostart-disabled.json").unlink(missing_ok=True)

    def test_wm_command_name(self):
        self.assertEqual(autostart.wm_command_name("uwsm app -- firefox --x"), "firefox")
        self.assertEqual(autostart.wm_command_name("--no-startup-id nm-applet"), "nm-applet")
