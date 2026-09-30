import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins" / "nitaaq-store" / "skills" / "nitaaq-store"
FIX = Path(__file__).resolve().parent / "fixtures"
for p in (str(SKILL / "scripts"), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)
