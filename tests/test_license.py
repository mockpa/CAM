# SPDX-License-Identifier: GPL-3.0-or-later
"""Every first-party source file names the project license."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HEADER = "SPDX-License-Identifier: GPL-3.0-or-later"


def sources():
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard",
                              "*.py", "*.rs", "*.sh"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.split() if not f.startswith("camcore/vendor/") and (ROOT / f).exists()]


def test_every_source_file_has_the_spdx_header():
    files = sources()
    assert files
    missing = [f for f in files if HEADER not in "".join((ROOT / f).read_text().splitlines(True)[:2])]
    assert missing == []
