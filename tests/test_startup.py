from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_ui_import_does_not_load_numpy_or_pandas() -> None:
    script = (
        "import sys; import network_doctor.ui; "
        "assert 'numpy' not in sys.modules, 'numpy imported at startup'; "
        "assert 'pandas' not in sys.modules, 'pandas imported at startup'"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
