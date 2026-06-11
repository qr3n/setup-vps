# setup_vps/steps/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class StepResult:
    success: bool
    message: str = ""
    error: str = ""


@dataclass
class VerifyResult:
    passed: bool
    checks: dict[str, str] = field(default_factory=dict)
    message: str = ""


class BaseStep(ABC):
    name: str = ""
    title: str = ""
    description: str = ""

    @abstractmethod
    def preflight(self, config, state) -> bool:
        """Return True if step is already complete (idempotent check). Skip run() if True."""

    @abstractmethod
    def run(self, config, state) -> StepResult:
        """Execute the step. Raise on fatal errors."""

    @abstractmethod
    def verify(self, config, state) -> VerifyResult:
        """Verify changes were applied. Returns VerifyResult with per-check details."""

    def rollback(self, config, state) -> None:
        """Best-effort rollback. Override if rollback is meaningful."""
        pass

    def log_path(self, logs_dir: str = "logs") -> "Path":
        from pathlib import Path
        return Path(logs_dir) / f"{self.name}.log"
