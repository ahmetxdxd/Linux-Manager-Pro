"""Tests for lmp.core.autostart."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from lmp.core import autostart
from lmp.core.util import CmdResult, data_dir


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


SHOW_TARGETS = """Id=default.target
Requires=basic.target
Wants=psd.service lmp-autostart-guard.path

Id=graphical-session.target
Requires=basic.target
Wants=perch.service graphical-session-pre.target

Id=plasma-workspace.target
Requires=plasma-core.target
Wants=plasma-kaccess.service plasma-powerdevil.service xdg-desktop-autostart.target
"""

SHOW_UNITS = """Id=plasma-kaccess.service
Description=KAccess
LoadState=loaded
ActiveState=active
SubState=running
FragmentPath=/usr/lib/systemd/user/plasma-kaccess.service

Id=plasma-powerdevil.service
Description=Powerdevil
LoadState=loaded
ActiveState=inactive
SubState=dead
FragmentPath=/usr/lib/systemd/user/plasma-powerdevil.service

Id=psd.service
Description=Profile-sync-daemon
LoadState=loaded
ActiveState=active
SubState=running
FragmentPath=/usr/lib/systemd/user/psd.service

Id=perch.service
Description=Perch
LoadState=loaded
ActiveState=active
SubState=running
FragmentPath=/home/x/.config/systemd/user/perch.service

Id=arch-update.timer
Description=Arch update check
LoadState=loaded
ActiveState=active
SubState=waiting
FragmentPath=/etc/systemd/user/arch-update.timer

Id=xdg-user-dirs.service
Description=User folders update
LoadState=loaded
ActiveState=active
SubState=exited
FragmentPath=/etc/systemd/user/xdg-user-dirs.service
"""

UNIT_FILES = """arch-update.timer      enabled enabled
xdg-user-dirs.service  enabled enabled
lmp-autostart-guard.path enabled enabled
"""


def _proc(comm, unit, *names):
    return autostart.ProcInfo(comm=comm, names=frozenset({comm, *names}), unit=unit,
                              cgroup=f"/user.slice/{unit}")


class RuntimeTests(unittest.TestCase):
    """What is really running; read-only, so systemctl and /proc are faked."""

    def setUp(self):
        shutil.rmtree(autostart.user_dir(), ignore_errors=True)
        autostart._blocked_path().unlink(missing_ok=True)
        (data_dir() / "autostart-disabled.json").unlink(missing_ok=True)
        (data_dir() / "autostart-services.json").unlink(missing_ok=True)
        self.empty = Path(tempfile.mkdtemp())
        os.environ["XDG_CONFIG_DIRS"] = str(self.empty)      # no /etc/xdg entries
        self.addCleanup(os.environ.pop, "XDG_CONFIG_DIRS", None)

    def _snapshot(self, procs=(), pulled=None, units=None):
        return autostart.Snapshot(units=units or {}, pulled_by=pulled or {}, procs=list(procs))

    def test_unit_names(self):
        self.assertEqual(autostart.app_unit_name("org.kde.kdeconnect.daemon.desktop"),
                         "app-org.kde.kdeconnect.daemon@autostart.service")
        self.assertEqual(autostart.app_unit_name("gnome-keyring-pkcs11.desktop"),
                         "app-gnome\\x2dkeyring\\x2dpkcs11@autostart.service")

    def test_parse_show(self):
        data = autostart.parse_show(SHOW_UNITS)
        self.assertEqual(data["plasma-kaccess.service"]["Description"], "KAccess")
        self.assertEqual(data["arch-update.timer"]["SubState"], "waiting")
        self.assertEqual(len(data), 6)
        self.assertEqual(autostart.parse_show(""), {})

    def test_item_binary(self):
        self.assertEqual(autostart._item_binary("kaccess"), "kaccess")
        self.assertEqual(autostart._item_binary("/usr/lib/org_kde_powerdevil"), "org_kde_powerdevil")
        self.assertEqual(autostart._item_binary('/bin/sh -c "sleep 3 && arch-update --tray"'),
                         "arch-update")
        self.assertEqual(autostart._item_binary("sh -c 'sleep 5; exec foo --x'"), "foo")
        self.assertEqual(autostart._item_binary("flatpak run com.x.Y"), "com.x.Y")
        # a bare shell or interpreter says nothing about which program it is
        self.assertEqual(autostart._item_binary("sh -c 'sleep 5'"), "")
        self.assertEqual(autostart._item_binary("bash"), "")
        self.assertEqual(autostart._item_binary("env"), "")

    def test_scan_processes(self):
        root = Path(tempfile.mkdtemp())
        proc = root / "4242"
        proc.mkdir()
        (proc / "comm").write_text("org_kde_powerde\n")        # cut at 15 characters
        (proc / "cmdline").write_bytes(b"/usr/lib/org_kde_powerdevil\0")
        (proc / "cgroup").write_text(
            "0::/user.slice/user-1000.slice/user@1000.service/background.slice/"
            "plasma-powerdevil.service\n")
        os.symlink("/usr/lib/org_kde_powerdevil", proc / "exe")
        script = root / "4343"
        script.mkdir()
        (script / "comm").write_text("python3\n")
        (script / "cmdline").write_bytes(b"/usr/bin/python3\0/opt/app/tray.py\0")
        (script / "cgroup").write_text("0::/user.slice/user-1000.slice/session-3.scope\n")
        os.symlink("/usr/bin/python3", script / "exe")
        (root / "self").mkdir()                                 # not a pid
        (root / "9999").mkdir()                                 # vanished: no files inside

        snap = autostart.Snapshot({}, {"plasma-powerdevil.service": "plasma-workspace.target"},
                                  autostart.scan_processes(root))
        self.assertEqual(len(snap.procs), 2)
        found = snap.find_process("org_kde_powerdevil")         # full name, though comm is cut
        self.assertEqual(found.unit, "plasma-powerdevil.service")
        self.assertEqual(snap.find_process("tray.py").unit, "session-3.scope")
        self.assertIsNone(snap.find_process("kaccess"))
        self.assertIsNone(snap.find_process(""))

    def test_take_snapshot_reads_systemctl(self):
        def fake_run(cmd, **_kw):
            cmd = list(cmd)
            if "list-unit-files" in cmd:
                return CmdResult(0, UNIT_FILES, "")
            if "default.target" in cmd:
                return CmdResult(0, SHOW_TARGETS, "")
            return CmdResult(0, SHOW_UNITS, "")
        with mock.patch.object(autostart, "run", fake_run):
            snap = autostart.take_snapshot(proc_root=self.empty)
        self.assertEqual(snap.pulled_by["plasma-kaccess.service"], "plasma-workspace.target")
        self.assertEqual(snap.pulled_by["psd.service"], "default.target")
        self.assertEqual(snap.pulled_by["arch-update.timer"], "enabled")
        self.assertNotIn("graphical-session-pre.target", snap.pulled_by)    # not a service
        self.assertNotIn("xdg-desktop-autostart.target", snap.pulled_by)
        self.assertNotIn("lmp-autostart-guard.path", snap.pulled_by)        # our own guard
        self.assertTrue(snap.units["plasma-kaccess.service"].running)
        self.assertFalse(snap.units["plasma-powerdevil.service"].running)
        self.assertFalse(snap.units["xdg-user-dirs.service"].running)       # finished one-shot

    def test_systemd_unavailable_is_not_an_error(self):
        gone = CmdResult(127, "", "systemctl: command not found")
        with mock.patch.object(autostart, "run", lambda *a, **k: gone):
            snap = autostart.take_snapshot(proc_root=self.empty)
        self.assertEqual((snap.units, snap.pulled_by, snap.procs), ({}, {}, []))
        autostart.add_item("Chat", "chat-app")
        items = autostart.inspect_items(autostart.list_items(), snap)
        self.assertEqual([i.name for i in items], ["Chat"])
        self.assertFalse(items[0].running)

    def test_switched_off_entry_still_started_by_a_session_service(self):
        autostart.add_item("Accessibility", "kaccess")
        autostart.add_item("Mine", "mine-app")
        autostart.add_item("Manual", "manual-app")
        for name in ("Accessibility", "Manual"):
            autostart.set_enabled(next(i for i in autostart.list_items() if i.name == name), False)
        snap = self._snapshot(
            procs=[_proc("kaccess", "plasma-kaccess.service"),
                   _proc("mine-app", "app-mine-app@autostart.service"),
                   _proc("manual-app", "app-manual-app-1234.scope")],
            pulled={"plasma-kaccess.service": "plasma-workspace.target"})
        items = {i.name: i for i in autostart.inspect_items(autostart.list_items(), snap)}

        kaccess = items["Accessibility"]
        self.assertFalse(kaccess.enabled)
        self.assertTrue(kaccess.running)
        self.assertEqual(kaccess.runner, "plasma-kaccess.service")
        self.assertTrue(kaccess.other_start)

        manual = items["Manual"]            # off, running because the user started it by hand
        self.assertTrue(manual.running)
        self.assertFalse(manual.other_start)

        mine = items["Mine"]                # on, running as it should
        self.assertTrue(mine.running)
        self.assertFalse(mine.other_start)

    def test_entry_running_through_its_own_autostart_unit(self):
        autostart.add_item("Chat", "chat-app")
        item = autostart.list_items()[0]
        own = autostart.app_unit_name(item.desktop_id)
        snap = self._snapshot(units={own: autostart.UnitInfo(own, load="loaded", active="active",
                                                             sub="running")})
        item = autostart.inspect_items([item], snap)[0]
        self.assertTrue(item.running)
        self.assertEqual(item.runner, own)
        self.assertFalse(item.other_start)

    def test_unknown_program_is_not_reported(self):
        autostart.add_item("Wrapper", "sh -c 'sleep 5'")
        snap = self._snapshot(procs=[_proc("sh", "session-1.scope")])
        item = autostart.inspect_items(autostart.list_items(), snap)[0]
        self.assertIsNone(item.running)           # cannot tell, so say nothing
        self.assertFalse(item.other_start)

    def test_session_services_are_listed(self):
        wants = systemd_wants = autostart.systemd_user_dir() / "default.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        (autostart.systemd_user_dir() / "mine.service").write_text("[Unit]\nDescription=Mine\n")
        (wants / "mine.service").symlink_to(autostart.systemd_user_dir() / "mine.service")
        self.addCleanup(shutil.rmtree, autostart.systemd_user_dir(), ignore_errors=True)
        self.assertTrue(systemd_wants.is_dir())

        units = autostart.parse_show(SHOW_UNITS)
        info = {n: autostart.UnitInfo(n, p["Description"], p["LoadState"], p["ActiveState"],
                                      p["SubState"], p["FragmentPath"]) for n, p in units.items()}
        info["mine.service"] = autostart.UnitInfo("mine.service", "Mine", "loaded", "active",
                                                  "running")
        for name in ("plasma-plasmashell.service",):
            info[name] = autostart.UnitInfo(name, "Plasma", "loaded", "active", "running",
                                            "/usr/lib/systemd/user/" + name)
        pulled = {"plasma-kaccess.service": "plasma-workspace.target",
                  "plasma-powerdevil.service": "plasma-workspace.target",
                  "plasma-plasmashell.service": "plasma-workspace.target",
                  "mine.service": "default.target", "gone.service": "default.target"}
        snap = self._snapshot(units=info, pulled=pulled)
        items = autostart.inspect_items(autostart.list_items(), snap)
        services = {i.desktop_id: i for i in items if i.kind == autostart.SERVICE}

        self.assertEqual(sorted(services),
                         ["mine.service", "plasma-kaccess.service", "plasma-plasmashell.service",
                          "plasma-powerdevil.service"])
        mine = services["mine.service"]                            # the user's own: plain disable
        self.assertFalse(mine.readonly or mine.via_mask)
        self.assertTrue(mine.running)
        kaccess = services["plasma-kaccess.service"]               # a system unit: switched by mask
        self.assertTrue(kaccess.via_mask)
        self.assertFalse(kaccess.readonly)
        self.assertTrue(kaccess.enabled)
        self.assertTrue(kaccess.running)
        self.assertEqual(kaccess.name, "KAccess")
        self.assertEqual(kaccess.origin, "system")
        self.assertEqual(kaccess.risk, "")
        powerdevil = services["plasma-powerdevil.service"]
        self.assertEqual(powerdevil.risk, "warn")                  # asks first
        self.assertTrue(powerdevil.via_mask)
        self.assertFalse(powerdevil.running)
        shell = services["plasma-plasmashell.service"]             # the desktop needs it
        self.assertTrue(shell.readonly)
        self.assertFalse(shell.via_mask)
        self.assertEqual(shell.risk, "block")
        with self.assertRaises(PermissionError):
            autostart.set_enabled(shell, False)

    def test_disable_all_ignores_system_services(self):
        autostart.add_item("Mine", "mine-app")
        units = {"plasma-kaccess.service": autostart.UnitInfo(
            "plasma-kaccess.service", "KAccess", "loaded", "active", "running",
            "/usr/lib/systemd/user/plasma-kaccess.service")}
        snap = self._snapshot(units=units, pulled={"plasma-kaccess.service": "plasma-workspace.target"})
        items = autostart.inspect_items(autostart.list_items(), snap)
        self.assertEqual(autostart.disable_all(items), [autostart.list_items()[0].desktop_id])
        self.assertTrue(next(i for i in items if i.kind == autostart.SERVICE).enabled)


class MaskTests(unittest.TestCase):
    """Switching system units off by masking them (systemctl is faked)."""

    def setUp(self):
        autostart._masked_path().unlink(missing_ok=True)
        shutil.rmtree(autostart.systemd_user_dir(), ignore_errors=True)
        self.addCleanup(shutil.rmtree, autostart.systemd_user_dir(), ignore_errors=True)
        self.calls = []
        self.fail = False

        def fake_run(cmd, **_kw):
            self.calls.append(list(cmd))
            return CmdResult(1, "", "File exists") if self.fail else CmdResult(0, "", "")
        patcher = mock.patch.object(autostart, "run", fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _item(self, unit="plasma-kaccess.service", **kw):
        return autostart.AutostartItem(
            desktop_id=unit, name=unit, command=unit, comment="", icon="", enabled=True,
            origin="system", user_path=None, system_path=None, kind=autostart.SERVICE,
            via_mask=True, **kw)

    def test_switch_off_masks_and_on_unmasks(self):
        item = self._item()
        autostart.set_enabled(item, False)
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "mask", item.desktop_id])
        self.assertEqual(autostart.masked_by_us(), [item.desktop_id])
        autostart.set_enabled(item, True)
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "unmask", item.desktop_id])
        self.assertEqual(autostart.masked_by_us(), [])

    def test_failure_is_reported_and_not_remembered(self):
        self.fail = True
        with self.assertRaises(OSError):
            autostart.set_enabled(self._item(), False)
        self.assertEqual(autostart.masked_by_us(), [])

    def test_bad_names_and_critical_units_are_refused(self):
        with self.assertRaises(ValueError):
            autostart.set_enabled(self._item("../evil.service"), False)
        with self.assertRaises(ValueError):
            autostart.set_enabled(self._item("x.service --now"), False)
        # even if a caller forgets the read-only flag, a critical unit is not masked
        with self.assertRaises(PermissionError):
            autostart.set_enabled(self._item("plasma-plasmashell.service"), False)
        self.assertEqual(self.calls, [])

    def test_unit_risk(self):
        self.assertEqual(autostart.unit_risk("plasma-kwin_wayland.service")[0], "block")
        self.assertEqual(autostart.unit_risk("plasma-polkit-agent.service")[0], "warn")
        self.assertEqual(autostart.unit_risk("plasma-kaccess.service"), ("", ""))

    def test_unmask_all_keeps_what_failed(self):
        for unit in ("plasma-kaccess.service", "arch-update.timer"):
            autostart.set_enabled(self._item(unit), False)
        self.fail = True
        self.assertEqual(autostart.unmask_all(), 0)
        self.assertEqual(len(autostart.masked_by_us()), 2)
        self.fail = False
        self.assertEqual(autostart.unmask_all(), 2)
        self.assertEqual(autostart.masked_by_us(), [])

    def test_masked_unit_is_shown_off_and_can_be_switched_back_on(self):
        link = autostart.systemd_user_dir() / "plasma-kaccess.service"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("/dev/null")
        masked = autostart.UnitInfo("plasma-kaccess.service", "KAccess", "masked", "inactive",
                                    "dead", str(link))
        snap = autostart.Snapshot(
            units={masked.name: masked},
            pulled_by={masked.name: "plasma-workspace.target"}, procs=[])
        item = autostart.inspect_items([], snap)[0]
        self.assertFalse(item.enabled)
        self.assertTrue(item.via_mask)
        self.assertFalse(item.readonly)
        self.assertEqual(item.origin, "system")          # not mistaken for a user's own unit
        self.assertFalse(snap.is_session_unit(masked.name))

    def test_masked_by_the_system_cannot_be_unmasked_here(self):
        masked = autostart.UnitInfo("foo.service", "Foo", "masked", "inactive", "dead", "")
        snap = autostart.Snapshot({masked.name: masked}, {masked.name: "default.target"}, [])
        item = autostart.inspect_items([], snap)[0]
        self.assertFalse(item.enabled)
        self.assertTrue(item.readonly)                   # no link of ours in the user folder

    def test_masking_stops_the_still_started_warning(self):
        autostart.add_item("Accessibility", "kaccess")
        entry = autostart.list_items()[0]
        autostart.set_enabled(entry, False)
        link = autostart.systemd_user_dir() / "plasma-kaccess.service"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("/dev/null")
        masked = autostart.UnitInfo("plasma-kaccess.service", "KAccess", "masked", "inactive",
                                    "dead", str(link))
        proc = autostart.ProcInfo("kaccess", frozenset({"kaccess"}), "plasma-kaccess.service", "")
        snap = autostart.Snapshot({masked.name: masked}, {masked.name: "plasma-workspace.target"},
                                  [proc])
        item = next(i for i in autostart.inspect_items([entry], snap) if i.kind == autostart.APP)
        self.assertTrue(item.running)                    # still running until it quits
        self.assertFalse(item.other_start)               # but it will not come back


def _write_entry(name, exec_line, extra=""):
    autostart.user_dir().mkdir(parents=True, exist_ok=True)
    path = autostart.user_dir() / name
    path.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n{extra}")
    return path


class GuardTests(unittest.TestCase):
    """The guard keeps a switched-off program off, even if its entry comes back renamed."""

    def setUp(self):
        shutil.rmtree(autostart.user_dir(), ignore_errors=True)
        for path in (autostart._blocked_path(), autostart._programs_path(),
                     autostart._guard_last_path()):
            path.unlink(missing_ok=True)
        self.empty = Path(tempfile.mkdtemp())
        os.environ["XDG_CONFIG_DIRS"] = str(self.empty)
        self.addCleanup(os.environ.pop, "XDG_CONFIG_DIRS", None)

    def _block(self, name, command):
        _write_entry(name, command)
        item = next(i for i in autostart.list_items() if i.desktop_id == name)
        autostart.set_enabled(item, False)
        return item

    def test_program_is_remembered_with_the_block(self):
        self._block("chat.desktop", "/opt/chat/chat-app --background")
        self.assertEqual(autostart._load_programs(), {"chat.desktop": "chat-app"})
        self.assertEqual(autostart.blocked_ids(), ["chat.desktop"])       # old file format kept

    def test_entry_written_again_under_another_name_is_hidden(self):
        self._block("chat.desktop", "chat-app")
        renamed = _write_entry("com.chat.App.desktop", "/opt/chat/chat-app --autostart")
        other = _write_entry("other.desktop", "other-app")
        mine = _write_entry("mine.desktop", "chat-app", "X-LMP-Created=true\n")
        autostart.enforce_blocked()
        self.assertIn("Hidden=true", renamed.read_text())
        self.assertNotIn("Hidden", other.read_text())          # a different program
        self.assertNotIn("Hidden", mine.read_text())           # made by "Add program"
        when, names = autostart.guard_last()
        self.assertEqual(names, ["com.chat.App.desktop"])
        self.assertLess(abs(when - time.time()), 60)

    def test_flatpak_entry_is_recognised_by_application_id(self):
        self._block("chat.desktop", "flatpak run com.chat.App")
        renamed = _write_entry("flatpak-chat.desktop",
                               "/usr/bin/flatpak run --branch=stable --command=chat com.chat.App")
        autostart.enforce_blocked()
        self.assertIn("Hidden=true", renamed.read_text())

    def test_wrapped_command_is_recognised(self):
        self._block("tray.desktop", 'sh -c "sleep 3 && arch-update --tray"')
        self.assertEqual(autostart._load_programs()["tray.desktop"], "arch-update")
        renamed = _write_entry("tray2.desktop", "arch-update --tray")
        autostart.enforce_blocked()
        self.assertIn("Hidden=true", renamed.read_text())

    def test_names_too_common_to_tell_programs_apart_are_not_matched(self):
        self._block("geoclue.desktop", "/usr/lib/geoclue-2.0/demos/agent")
        self.assertEqual(autostart._load_programs(), {})
        unrelated = _write_entry("other.desktop", "/opt/other/agent")
        autostart.enforce_blocked()
        self.assertNotIn("Hidden", unrelated.read_text())

    def test_unknown_program_is_matched_by_file_name_only(self):
        self._block("wrap.desktop", "sh -c 'sleep 5'")                   # no program to compare
        self.assertEqual(autostart._load_programs(), {})
        unrelated = _write_entry("x.desktop", "sh -c 'sleep 5'")
        autostart.enforce_blocked()
        self.assertNotIn("Hidden", unrelated.read_text())

    def test_switching_on_is_not_undone_by_a_twin_entry(self):
        a = self._block("a.desktop", "chat-app")
        self._block("b.desktop", "chat-app")
        a = next(i for i in autostart.list_items() if i.desktop_id == "a.desktop")
        autostart.set_enabled(a, True)
        autostart.enforce_blocked()
        items = {i.desktop_id: i for i in autostart.list_items()}
        self.assertTrue(items["a.desktop"].enabled)
        self.assertFalse(items["b.desktop"].enabled)
        self.assertEqual(autostart._load_programs(), {})

    def test_removing_the_block_forgets_the_program(self):
        item = self._block("chat.desktop", "chat-app")
        autostart.set_enabled(item, True)
        self.assertEqual((autostart.blocked_ids(), autostart._load_programs()), ([], {}))

    def test_old_blocks_get_their_program_from_the_entry_file(self):
        _write_entry("chat.desktop", "/opt/chat/chat-app", "Hidden=true\n")
        autostart._blocked_path().write_text('["chat.desktop"]')          # made by an older version
        autostart._fill_missing_programs()
        self.assertEqual(autostart._load_programs(), {"chat.desktop": "chat-app"})

    def test_script_runs_on_its_own(self):
        self._block("chat.desktop", "chat-app")
        script = Path(tempfile.mkdtemp()) / "guard.py"
        script.write_text(autostart.GUARD_SCRIPT)
        renamed = _write_entry("new.desktop", "chat-app")
        link = autostart.user_dir() / "link.desktop"
        link.symlink_to(renamed)
        res = subprocess.run([sys.executable, "-I", str(script), str(autostart._blocked_path()),
                              str(autostart.user_dir())], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("kept disabled: new.desktop", res.stdout)
        self.assertIn("Hidden=true", renamed.read_text())
        # a missing folder or list is not an error
        for args in ((str(autostart._blocked_path()), "/nonexistent"), ("/nonexistent.json", "/tmp")):
            self.assertEqual(subprocess.run([sys.executable, "-I", str(script), *args]).returncode, 0)

    def test_script_and_app_agree_on_the_program(self):
        namespace: dict = {}
        exec(compile(autostart.GUARD_SCRIPT, "guard", "exec"), namespace)
        for command in ("kaccess", "/usr/lib/org_kde_powerdevil --x %U", "env A=1 /opt/x/tool",
                        "flatpak run com.x.Y", "/usr/bin/flatpak run --branch=stable com.x.Y",
                        'sh -c "sleep 3 && arch-update --tray"', "sh -c 'sleep 5; exec foo --x'",
                        "sh -c 'sleep 5'", "bash", "env", "python3 /opt/app/tray.py",
                        "/usr/lib/geoclue-2.0/demos/agent"):
            self.assertEqual(namespace["binary_of"](command), autostart._item_binary(command),
                             command)


class SyncGuardTests(unittest.TestCase):
    """Installing the guard and keeping it running (systemctl is faked)."""

    def setUp(self):
        shutil.rmtree(autostart.user_dir(), ignore_errors=True)
        shutil.rmtree(autostart.systemd_user_dir(), ignore_errors=True)
        autostart._blocked_path().unlink(missing_ok=True)
        autostart._programs_path().unlink(missing_ok=True)
        self.addCleanup(shutil.rmtree, autostart.systemd_user_dir(), ignore_errors=True)
        self.calls = []
        self.enabled = self.active = False

        def fake_run(cmd, **_kw):
            cmd = list(cmd)
            self.calls.append(cmd[2:])
            if cmd[2] == "is-enabled":
                return CmdResult(0 if self.enabled else 1, "", "")
            if cmd[2] == "is-active":
                return CmdResult(0 if self.active else 3, "", "")
            return CmdResult(0, "", "")
        for patcher in (mock.patch.object(autostart, "run", fake_run),
                        mock.patch.object(autostart.shutil, "which", lambda _n: "/usr/bin/systemctl")):
            patcher.start()
            self.addCleanup(patcher.stop)
        _write_entry("chat.desktop", "chat-app")
        autostart.set_enabled(next(i for i in autostart.list_items() if i.name == "chat.desktop"),
                              False)
        self.calls.clear()

    def _verbs(self):
        return [c[0] for c in self.calls]

    def test_installs_enables_and_never_hits_the_start_limit(self):
        self.assertTrue(autostart.sync_guard())
        service = (autostart.systemd_user_dir() / f"{autostart.GUARD_UNIT}.service").read_text()
        self.assertIn("StartLimitIntervalSec=0", service)
        self.assertEqual(self._verbs(), ["daemon-reload", "is-enabled", "enable"])
        self.assertTrue(autostart.guard_script_path().exists())

    def test_nothing_to_do_when_it_is_already_running(self):
        autostart.sync_guard()
        self.enabled = self.active = True
        self.calls.clear()
        self.assertTrue(autostart.sync_guard())
        self.assertEqual(self._verbs(), ["is-enabled", "is-active"])

    def test_started_again_when_it_stopped(self):
        autostart.sync_guard()
        self.enabled, self.active = True, False
        self.calls.clear()
        autostart.sync_guard()
        self.assertEqual(self._verbs(), ["is-enabled", "is-active", "start"])

    def test_switched_on_again_when_somebody_disabled_it(self):
        autostart.sync_guard()
        self.enabled = False
        self.calls.clear()
        autostart.sync_guard()
        self.assertEqual(self._verbs(), ["is-enabled", "enable"])

    def test_old_unit_files_are_replaced_and_the_unit_restarted(self):
        autostart.sync_guard()
        service = autostart.systemd_user_dir() / f"{autostart.GUARD_UNIT}.service"
        service.write_text(service.read_text().replace("StartLimitIntervalSec=0\n", ""))
        self.enabled = self.active = True
        self.calls.clear()
        autostart.sync_guard()
        self.assertIn("StartLimitIntervalSec=0", service.read_text())
        self.assertEqual(self._verbs(), ["daemon-reload", "is-enabled", "restart"])

    def test_removed_when_nothing_is_blocked(self):
        autostart.sync_guard()
        autostart._blocked_path().write_text("[]")
        self.calls.clear()
        self.assertFalse(autostart.sync_guard())
        self.assertFalse((autostart.systemd_user_dir() / f"{autostart.GUARD_UNIT}.path").exists())
        self.assertEqual(self._verbs(), ["disable", "daemon-reload"])


class StopNowTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.fail = False

        def fake_run(cmd, **_kw):
            self.calls.append(list(cmd))
            return CmdResult(1, "", "Failed to stop") if self.fail else CmdResult(0, "", "")
        patcher = mock.patch.object(autostart, "run", fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _item(self, runner, running=True):
        return autostart.AutostartItem(
            desktop_id="x.desktop", name="X", command="x", comment="", icon="", enabled=False,
            origin="user", user_path=None, system_path=None, running=running, runner=runner)

    def test_stops_the_unit_the_program_runs_in(self):
        for runner in ("plasma-kaccess.service", "app-gnome\\x2dkeyring@autostart.service",
                       "app-flatpak-com.x.Y-123.scope"):
            item = self._item(runner)
            self.assertTrue(autostart.can_stop(item), runner)
            autostart.stop_now(item)
            self.assertEqual(self.calls[-1], ["systemctl", "--user", "stop", runner])

    def test_never_stops_the_session_or_the_desktop(self):
        for runner in ("session-3.scope", "user@1000.service", "init.scope", "",
                       "plasma-plasmashell.service", "plasma-kwin_wayland.service",
                       "dbus-broker.service", "lmp-autostart-guard.service", "-x.service",
                       "x.service --now", "../x.service", "not-a-unit"):
            item = self._item(runner)
            self.assertFalse(autostart.can_stop(item), runner)
            with self.assertRaises(PermissionError):
                autostart.stop_now(item)
        self.assertEqual(self.calls, [])

    def test_only_what_is_running(self):
        self.assertFalse(autostart.can_stop(self._item("plasma-kaccess.service", running=False)))
        self.assertFalse(autostart.can_stop(self._item("plasma-kaccess.service", running=None)))

    def test_failure_is_reported(self):
        self.fail = True
        with self.assertRaises(OSError):
            autostart.stop_now(self._item("plasma-kaccess.service"))
