"""TeraCopy-style copying: copy, then compare every file byte by byte with its source."""

from __future__ import annotations

import os
import shutil
from typing import Callable, Iterable

from lmp.i18n import tr

CHUNK = 1024 * 1024
Progress = Callable[[int | None, str], None]


class VerifyError(Exception):
    pass


def _walk(src: str) -> Iterable[tuple[str, str]]:
    """(absolute path, path relative to the parent of src) for every entry under src."""
    base = os.path.dirname(os.path.abspath(src).rstrip("/"))
    yield src, os.path.relpath(src, base)
    if os.path.isdir(src) and not os.path.islink(src):
        for root, dirs, files in os.walk(src):
            for name in sorted(dirs) + sorted(files):
                full = os.path.join(root, name)
                yield full, os.path.relpath(full, base)


def same_bytes(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(CHUNK), fb.read(CHUNK)
            if ca != cb:
                return False
            if not ca:
                return True


def verify_trees(src: str, dest: str, progress: Progress | None = None) -> list[str]:
    """Compares dest with src byte by byte; returns the relative paths that differ."""
    bad = []
    for full, rel in _walk(src):
        rel = os.path.relpath(full, src) if full != src else "."
        other = os.path.normpath(os.path.join(dest, rel))
        if os.path.islink(full):
            if not os.path.islink(other) or os.readlink(full) != os.readlink(other):
                bad.append(rel)
        elif os.path.isdir(full):
            if not os.path.isdir(other):
                bad.append(rel)
        elif os.path.isfile(full):
            if progress:
                progress(None, tr("Verifying: {path}").format(path=rel))
            if not os.path.isfile(other) or not same_bytes(full, other):
                bad.append(rel)
    return bad


def copy_and_verify(sources: list[str], dest_dir: str, progress: Progress | None = None) -> str:
    """Copies files/folders into dest_dir, then verifies every byte. Raises VerifyError
    listing the files whose copy differs from the source."""
    entries = [(full, rel) for src in sources for full, rel in _walk(src)]
    total = sum(os.path.getsize(f) for f, _ in entries
                if os.path.isfile(f) and not os.path.islink(f)) or 1
    done = 0
    # 1) copy (0-50 %)
    for full, rel in entries:
        target = os.path.join(dest_dir, rel)
        if os.path.islink(full):
            if os.path.lexists(target):
                os.unlink(target)
            os.symlink(os.readlink(full), target)
        elif os.path.isdir(full):
            os.makedirs(target, exist_ok=True)
        elif os.path.isfile(full):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(full, "rb") as fi, open(target, "wb") as fo:
                while chunk := fi.read(CHUNK):
                    fo.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(int(done * 50 / total), tr("Copying: {path}").format(path=rel))
                fo.flush()
                os.fsync(fo.fileno())
            shutil.copystat(full, target)
    # 2) read both copies back and compare (50-100 %)
    bad, checked = [], 0
    for full, rel in entries:
        if os.path.isfile(full) and not os.path.islink(full):
            target = os.path.join(dest_dir, rel)
            if not same_bytes(full, target):
                bad.append(rel)
            checked += os.path.getsize(full)
            if progress:
                progress(50 + int(checked * 50 / total), tr("Verifying: {path}").format(path=rel))
    files = sum(1 for f, _ in entries if os.path.isfile(f) and not os.path.islink(f))
    if bad:
        raise VerifyError(tr("{n} files differ from the source: {names}").format(
            n=len(bad), names=", ".join(bad[:10])))
    return tr("{n} files copied and verified byte by byte, no differences.").format(n=files)
