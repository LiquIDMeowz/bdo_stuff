# tests/test_scaffolding.py
import subprocess
import sys


def test_module_runs():
    result = subprocess.run(
        [sys.executable, "-m", "bdo_profit", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "bdo_profit" in result.stdout
