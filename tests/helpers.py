"""Shared test fixtures. The fake HOME is created in conftest.py before lmp is imported."""

import os
from pathlib import Path

from lmp.core.apps import AppEntry

HOME = Path(os.environ["HOME"])

QI_SAMPLE = """Name            : obs-studio
Version         : 30.2.3-1
Description     : Free, open source software for live streaming and recording
Architecture    : x86_64
URL             : https://obsproject.com
Licenses        : GPL-2.0-or-later
Groups          : None
Provides        : None
Depends On      : ffmpeg  jansson  libxinerama
Optional Deps   : libfdk-aac: FDK AAC codec support
                  intel-media-driver: hardware encoding [installed]
Required By     : None
Optional For    : None
Conflicts With  : None
Replaces        : None
Installed Size  : 21.40 MiB
Packager        : Jonathan Steel <jsteel@archlinux.org>
Build Date      : Sat Aug 31 00:17:15 2024
Install Date    : Sun Sep  1 10:02:03 2024
Install Reason  : Explicitly installed
Install Script  : No
Validated By    : Signature

Name            : jansson
Version         : 2.14-4
Description     : C library for encoding, decoding and manipulating JSON data
Architecture    : x86_64
URL             : https://github.com/akheron/jansson
Licenses        : MIT
Groups          : None
Provides        : libjansson.so=4-64
Depends On      : glibc
Optional Deps   : None
Required By     : obs-studio  nftables
Optional For    : None
Conflicts With  : None
Replaces        : None
Installed Size  : 182.00 KiB
Packager        : Someone <x@archlinux.org>
Build Date      : Mon 15 Jan 2024 04:33:29 PM +03
Install Date    : Mon 15 Jan 2024 05:00:00 PM +03
Install Reason  : Installed as a dependency for another package
Install Script  : No
Validated By    : Signature
"""


def make_app(ident, name=None, source="repo", desktop_entries=()):
    return AppEntry(ident=ident, name=name or ident, source=source,
                    desktop_entries=list(desktop_entries))
