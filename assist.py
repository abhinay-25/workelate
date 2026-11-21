#!/usr/bin/env python3
"""Entry point for BuildSprint R3 assistant CLI."""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
