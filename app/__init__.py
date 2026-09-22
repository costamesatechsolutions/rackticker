"""RackTicker: one framebuffer, any output."""

import sys

__version__ = "1.5.0"

# The rest of the code is written in Python 3.10 (`int | None` in annotations, and
# those are evaluated at class-definition time), so an older interpreter fails on
# an import deep in the tree with a TypeError that says nothing about versions.
# macOS ships 3.9 and Debian 11 ships 3.9, so this is the first thing a lot of
# people meet. Say what is wrong while the message can still be read. This file is
# the package itself, so it runs before any other module here — keep it 3.7-simple.
if sys.version_info < (3, 10):
    sys.exit("RackTicker needs Python 3.10 or newer. This is Python %d.%d (%s).\n"
             "Make the virtual environment with a newer one, for example:\n"
             "    python3.12 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
             % (sys.version_info[0], sys.version_info[1], sys.executable))
