# tests/test_state.py
import json
import pytest
from pathlib import Path
from setup_vps.state import State, StepStatus


def test_state_initial_all_pending(tmp_path):
    state = State(path=tmp_path / "state.json", step_names=["s01", "s02"])
    assert state.get_status("s01") == StepStatus.PENDING
    assert state.get_status("s02") == StepStatus.PENDING


def test_state_mark_done(tmp_path):
    state = State(path=tmp_path / "state.json", step_names=["s01"])
    state.mark_done("s01")
    assert state.get_status("s01") == StepStatus.DONE


def test_state_mark_failed(tmp_path):
    state = State(path=tmp_path / "state.json", step_names=["s01"])
    state.mark_failed("s01", error="oops")
    assert state.get_status("s01") == StepStatus.FAILED


def test_state_persists_to_disk(tmp_path):
    path = tmp_path / "state.json"
    state = State(path=path, step_names=["s01"])
    state.mark_done("s01")
    # reload
    state2 = State(path=path, step_names=["s01"])
    assert state2.get_status("s01") == StepStatus.DONE


def test_state_config_hash_stale(tmp_path):
    state = State(path=tmp_path / "state.json", step_names=["s01"])
    state.mark_done("s01")
    state.set_config_hash("hash1")
    # simulate config change
    assert state.is_config_changed("hash2") is True
    assert state.is_config_changed("hash1") is False


def test_state_mark_stale(tmp_path):
    state = State(path=tmp_path / "state.json", step_names=["s01"])
    state.mark_done("s01")
    state.mark_stale("s01")
    assert state.get_status("s01") == StepStatus.STALE
