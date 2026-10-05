#!/usr/bin/env python3
"""PreToolUse hook: deny Salla store writes that have no matching armed approval.

Reads the hook event on stdin. Prints a deny decision, or nothing (the call
then goes through the host's normal permission flow). Logic and docs:
skills/nitaaq-store/scripts/nitaaq/gate.py. Standard library only.
"""

import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "nitaaq-store" / "scripts"))

from nitaaq.gate import hook_main  # noqa: E402

if __name__ == "__main__":
    out = hook_main(sys.stdin.read(), os.environ, str(PLUGIN_ROOT))
    if out:
        print(out)
    sys.exit(0)
