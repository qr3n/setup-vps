# setup_vps/state.py
import json
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STALE = "stale"
    SKIPPED = "skipped"


class State:
    def __init__(self, path: Path, step_names: list[str]):
        self.path = Path(path)
        self.step_names = step_names
        self._data: dict = {"config_hash": None, "steps": {}}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                pass
        # ensure all steps present
        if "steps" not in self._data:
            self._data["steps"] = {}
        for name in step_names:
            if name not in self._data["steps"]:
                self._data["steps"][name] = {"status": StepStatus.PENDING}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, default=str))

    def get_status(self, step_name: str) -> StepStatus:
        return StepStatus(self._data["steps"].get(step_name, {}).get("status", StepStatus.PENDING))

    def mark_done(self, step_name: str):
        self._data["steps"][step_name] = {
            "status": StepStatus.DONE,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def mark_failed(self, step_name: str, error: str = ""):
        self._data["steps"][step_name] = {
            "status": StepStatus.FAILED,
            "error": error,
            "attempted_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def mark_running(self, step_name: str):
        self._data["steps"][step_name]["status"] = StepStatus.RUNNING
        self._save()

    def mark_stale(self, step_name: str):
        self._data["steps"][step_name]["status"] = StepStatus.STALE
        self._save()

    def mark_pending(self, step_name: str):
        self._data["steps"][step_name] = {"status": StepStatus.PENDING}
        self._save()

    def set_config_hash(self, hash_value: str):
        self._data["config_hash"] = hash_value
        self._save()

    def is_config_changed(self, current_hash: str) -> bool:
        return self._data.get("config_hash") != current_hash

    def all_steps(self) -> list[tuple[str, StepStatus]]:
        return [(name, self.get_status(name)) for name in self.step_names]

    def done_count(self) -> int:
        return sum(1 for _, s in self.all_steps() if s == StepStatus.DONE)
