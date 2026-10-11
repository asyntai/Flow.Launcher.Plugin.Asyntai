# Copyright (c) Asyntai. Licensed under the MIT license.
"""Entry point Flow Launcher runs for every query and every action."""

import sys
from pathlib import Path

plugindir = Path(__file__).resolve().parent
sys.path = [str(plugindir / p) for p in (".", "lib", "plugin")] + sys.path

from plugin.app import run  # noqa: E402

if __name__ == "__main__":
    run(sys.argv)
