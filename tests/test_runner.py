# tests/test_runner.py
import subprocess
import pytest
from pathlib import Path
from setup_vps.runner import run_cmd, CommandResult

def test_run_cmd_success():
    result = run_cmd(["echo", "hello"])
    assert result.returncode == 0
    assert "hello" in result.stdout

def test_run_cmd_failure():
    result = run_cmd(["false"])
    assert result.returncode != 0

def test_run_cmd_capture():
    result = run_cmd(["echo", "captured"], capture=True)
    assert result.stdout.strip() == "captured"

def test_run_cmd_logs_to_file(tmp_path):
    log_file = tmp_path / "test.log"
    result = run_cmd(["echo", "logged"], log_path=log_file)
    assert log_file.exists()
    assert "logged" in log_file.read_text()
