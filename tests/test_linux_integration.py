"""Real Linux flock/process-group behavior with an isolated fake simulator."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from virtuoso_lan_bridge.runner import controller


@pytest.mark.skipif(sys.platform != "linux", reason="Requires real Linux process groups and flock")
def test_linux_worker_lifecycle(tmp_path):
    shutil.copy2(Path(controller.__file__).with_name("worker.py"), tmp_path / "worker.py")
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("linux_worker_smoke.py")), str(tmp_path)], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert b'"status": "PASS"' in result.stdout
