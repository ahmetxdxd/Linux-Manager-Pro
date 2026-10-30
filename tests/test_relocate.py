"""Tests for lmp.core.relocate."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lmp.core import relocate
from helpers import HOME


class RelocateTests(unittest.TestCase):
    def test_user_move_refuses_lossy_filesystems(self):
        src = HOME / "lossy-src"
        src.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(relocate, "_fs_type", return_value="exfat"):
            with self.assertRaises(relocate.RelocationError):
                relocate.move_user_dir(str(src), tempfile.mkdtemp())
        self.assertFalse(src.is_symlink())

    def test_copy_error_is_reported_not_hung(self):
        # a source that does not exist: rsync writes errors and exits non-zero
        with self.assertRaises(relocate.RelocationError) as ctx:
            relocate._copy_tree("/nonexistent-lmp-src", tempfile.mkdtemp() + "/d",
                                lambda *a, **k: None, "x")
        self.assertIn("x", str(ctx.exception))

    def test_candidate_dirs(self):
        files = ["/opt/foo/", "/opt/foo/bin/foo", "/usr/bin/foo", "/usr/share/foo/",
                 "/usr/share/foo/data.bin", "/usr/share/applications/foo.desktop",
                 "/usr/lib/libfoo.so", "/usr/share/icons/hicolor/48x48/apps/foo.png"]
        existing = {"/opt/foo", "/opt/foo/bin", "/usr/share/foo", "/usr/share/icons/hicolor"}

        def owners(paths):
            return {p: ({"foo"} if p in ("/opt/foo", "/opt/foo/bin", "/usr/share/foo")
                        else {"hicolor-icon-theme", "foo"}) for p in paths}

        with mock.patch("os.path.isdir", lambda p: p in existing), \
                mock.patch("os.path.islink", lambda p: False), \
                mock.patch("os.path.ismount", lambda p: False), \
                mock.patch.object(relocate, "dir_size", lambda p: 10):
            cands = relocate.candidate_dirs("foo", files, owners)
        self.assertEqual(sorted(c.path for c in cands), ["/opt/foo", "/usr/share/foo"])

    def test_disallowed(self):
        self.assertFalse(relocate._is_allowed_path("/usr/share/applications"))
        self.assertFalse(relocate._is_allowed_path("/usr/bin/foo"))
        self.assertFalse(relocate._is_allowed_path("/opt"))
        self.assertTrue(relocate._is_allowed_path("/opt/foo"))

    def test_hooks(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(relocate, "GUARD_HOOK", tmp / "g.hook"), \
                mock.patch.object(relocate, "CLEANUP_HOOK", tmp / "c.hook"), \
                mock.patch.object(relocate, "HOOK_DIR", tmp), \
                mock.patch.object(relocate, "STATE_DIR", tmp / "state"), \
                mock.patch.object(relocate, "HOOK_SCRIPT", tmp / "state/relocation-hook"), \
                mock.patch.object(relocate, "REGISTRY", tmp / "state/relocations.json"):
            rel = relocate.Relocation("foo", "/mnt/nope", "", "/mnt/nope/.l", "now",
                                      dirs=[relocate.MovedDir("/opt/foo", "/mnt/nope/x", 1)])
            relocate.save_registry({"foo": rel})
            relocate.write_hooks({"foo": rel})
            text = (tmp / "g.hook").read_text()
            self.assertIn("Target = foo", text)
            self.assertIn("AbortOnFail", text)
            # the hook runs a root-owned stand-alone script, never code from the app folder
            self.assertIn(f"Exec = /usr/bin/python3 -I {tmp}/state/relocation-hook guard", text)
            script = tmp / "state/relocation-hook"
            run = lambda names: subprocess.run([sys.executable, "-I", str(script), "guard"],
                                               input=names, text=True, capture_output=True)
            self.assertEqual(run("foo\nbar\n").returncode, 1, "disk missing: block the update")
            self.assertEqual(run("bar\n").returncode, 0)
            relocate.write_hooks({})
            self.assertFalse((tmp / "g.hook").exists())

    def test_guard_blocks_when_disconnected(self):
        rel = relocate.Relocation("foo", "/mnt/nope", "", "/mnt/nope/.l", "now",
                                  dirs=[relocate.MovedDir("/opt/foo", "/mnt/nope/x", 1)])
        with mock.patch.object(relocate, "load_registry", return_value={"foo": rel}):
            self.assertEqual(relocate.guard(["foo", "bar"]), 1)
            self.assertEqual(relocate.guard(["bar"]), 0)

    def test_user_dir_move_and_restore(self):
        src = HOME / ".bigdata"
        (src / "models").mkdir(parents=True, exist_ok=True)
        (src / "models/m.bin").write_text("x" * 5000)
        target = Path(tempfile.mkdtemp())
        item = relocate.move_user_dir(str(src), str(target))
        self.assertTrue(src.is_symlink())
        self.assertEqual((src / "models/m.bin").read_text(), "x" * 5000)
        relocate.restore_user_dir(item)
        self.assertFalse(src.is_symlink())
        self.assertTrue((src / "models/m.bin").exists())
        self.assertEqual(relocate.load_user_relocations(), [])


class VerifiedCopyTests(unittest.TestCase):
    def test_copy_and_detect_corruption(self):
        from lmp.core import verified_copy
        src = Path(tempfile.mkdtemp()) / "data"
        (src / "sub").mkdir(parents=True)
        (src / "sub/a.bin").write_bytes(b"x" * 3000)
        (src / "b.txt").write_text("hello")
        dest = tempfile.mkdtemp()
        self.assertIn("2", verified_copy.copy_and_verify([str(src)], dest))
        self.assertEqual(verified_copy.verify_trees(str(src), dest + "/data"), [])
        (Path(dest) / "data/sub/a.bin").write_bytes(b"x" * 2999 + b"y")   # one byte differs
        self.assertEqual(verified_copy.verify_trees(str(src), dest + "/data"), ["sub/a.bin"])
