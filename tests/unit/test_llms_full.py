"""docs/llms-full.txt must match what scripts/build_llms_full.py produces."""

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_llms_full_is_up_to_date():
    build = runpy.run_path(str(ROOT / "scripts" / "build_llms_full.py"))["build"]
    current = (ROOT / "docs" / "llms-full.txt").read_text()
    assert current == build(), "Run: python scripts/build_llms_full.py"
