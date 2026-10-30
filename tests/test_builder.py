"""Tests for lmp.core.builder."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from lmp.core import builder
from helpers import HOME


class BuilderTests(unittest.TestCase):
    def test_conf_rewrites_flags(self):
        etc = Path(tempfile.mkdtemp())
        sysconf = etc / "makepkg.conf"
        sysconf.write_text(
            'CFLAGS="-march=x86-64 -mtune=generic -O2 -pipe -fno-plt"\n'
            'CXXFLAGS="$CFLAGS -Wp,-D_GLIBCXX_ASSERTIONS"\n'
            'RUSTFLAGS="-Cforce-frame-pointers=yes"\n'
            "OPTIONS=(strip docs !libtool !staticlibs emptydirs zipman purge debug lto)\n")
        opts = builder.BuildOptions(opt_level="O3", lto=False, ccache=True, jobs=8)
        text = builder.makepkg_conf_text(opts).replace("/etc/makepkg.conf", str(sysconf))
        conf = etc / "lmp.conf"
        conf.write_text(text)
        out = subprocess.run(
            ["bash", "-c", f"source {conf}; echo \"$CFLAGS\"; echo \"$CXXFLAGS\"; "
                           "echo \"$RUSTFLAGS\"; echo \"${OPTIONS[*]}\"; echo \"$MAKEFLAGS\"; "
                           "echo \"${PACMAN_AUTH[*]}\""],
            capture_output=True, text=True, check=True).stdout.splitlines()
        cflags, cxxflags, rust, options, make, auth = out
        self.assertTrue(cflags.startswith("-march=native -mtune=native -O3"))
        self.assertNotIn("x86-64", cflags)
        self.assertNotIn("-O2", cflags)
        self.assertIn("-fno-plt", cflags)
        self.assertIn("_GLIBCXX_ASSERTIONS", cxxflags)
        self.assertTrue(rust.endswith("-C target-cpu=native -C opt-level=3"))
        self.assertIn("!lto", options.split())
        self.assertNotIn("lto", options.split())
        self.assertIn("ccache", options.split())
        self.assertIn("!debug", options.split())
        self.assertEqual(make, "-j8")
        self.assertEqual(auth, "pkexec")

    def test_git_pkgbuild_is_valid_bash(self):
        text = builder.git_pkgbuild("https://github.com/Some_User/My_Tool.git")
        res = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("pkgname=my-tool-git", text)
        self.assertNotIn("branch=master", text)

    def test_build_script_is_valid_bash(self):
        job = builder.BuildJob(source="git", target="https://github.com/a/b", name="b-git",
                               pkgbase="b-git", workdir=HOME / "w")
        script = builder.build_script(job, builder.BuildOptions(), HOME / "c.conf")
        res = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        job = builder.BuildJob(source="repo", target="zstd", name="zstd", pkgbase="zstd",
                               clone_url="https://x/zstd.git", tag="1-1.5.6-1",
                               workdir=HOME / "w")
        script = builder.build_script(job, builder.BuildOptions(skip_pgp=True), HOME / "c.conf")
        self.assertIn("--skippgpcheck", script)
        res = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_gitlab_names(self):
        self.assertEqual(builder.gitlab_project_name("gtk2+extra"), "gtk2-extra")
        self.assertEqual(builder.gitlab_project_name("libc++"), "libcplusplus")
        self.assertEqual(builder.gitlab_project_name("tree"), "unix-tree")
        self.assertEqual(builder.version_tag("1:2.3-1"), "1-2.3-1")
