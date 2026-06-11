# VPS Setup CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Python CLI/TUI tool that automates VLESS TCP REALITY + XHTTP + Hysteria2 VPN server setup on Ubuntu 22.04/Debian 12, running locally on the server with state persistence, idempotent steps, and per-step verification.

**Architecture:** Modular step system where each step (BaseStep subclass) implements preflight/run/verify/rollback. State persisted in `state.json` (checkpoint + idempotent). Interactive menus via prompt_toolkit (keyboard-only, SSH-friendly). Rich for all output/progress display.

**Tech Stack:** Python 3.11+, Rich 13+, prompt-toolkit 3+, Click 8+, PyYAML 6+

**Spec:** `docs/superpowers/specs/2026-06-11-vps-setup-cli-design.md`
**Guide:** `xray-server-guide-final.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Project metadata, deps, entry point |
| `setup_vps/__init__.py` | Package init |
| `setup_vps/main.py` | CLI entry (Click), main menu loop |
| `setup_vps/config.py` | config.yaml load/save/validate + wizard |
| `setup_vps/state.py` | state.json checkpoint: done/failed/pending/stale |
| `setup_vps/runner.py` | Shell executor: streaming output + log to file |
| `setup_vps/ui.py` | Rich console singleton + prompt_toolkit menus |
| `setup_vps/steps/__init__.py` | Step registry |
| `setup_vps/steps/base.py` | BaseStep, StepResult, VerifyResult |
| `setup_vps/steps/s01_system.py` | apt upgrade, swap 2GB, ulimits, hostname |
| `setup_vps/steps/s02_sysctl.py` | BBR, TCP buffers, sysctl apply |
| `setup_vps/steps/s03_ssh.py` | SSH hardening + knockd |
| `setup_vps/steps/s04_firewall.py` | UFW rules |
| `setup_vps/steps/s05_certificates.py` | certbot + 2 domains |
| `setup_vps/steps/s06_nginx.py` | nginx stream + http vhosts |
| `setup_vps/steps/s07_xray.py` | xray install + keygen + config |
| `setup_vps/steps/s08_verify.py` | Final verification of all steps |
| `tests/test_state.py` | State management tests |
| `tests/test_config.py` | Config load/validate tests |
| `tests/test_runner.py` | Runner tests |
| `tests/test_steps_base.py` | BaseStep interface tests |

---

## Task 1: Project Scaffold + pyproject.toml

**Files:**
- Create: `pyproject.toml`
- Create: `setup_vps/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`

- [ ] **Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "setup-vps"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "rich>=13.0",
    "prompt-toolkit>=3.0",
    "click>=8.1",
    "pyyaml>=6.0",
]

[project.scripts]
setup-vps = "setup_vps.main:cli"

[tool.hatch.build.targets.wheel]
packages = ["setup_vps"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create package files**

```python
# setup_vps/__init__.py
__version__ = "0.1.0"
```

```python
# tests/__init__.py
```

```
# .gitignore
__pycache__/
*.pyc
.venv/
state.json
logs/
config.yaml
*.egg-info/
dist/
```

- [ ] **Step 3: Install in dev mode**

```bash
cd /home/admin/PycharmProjects/setup-vps
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]" 2>/dev/null || pip install -e .
pip install pytest
```

Expected: no errors, `setup-vps` command available after install.

- [ ] **Step 4: Commit**

```bash
git init
git add pyproject.toml setup_vps/__init__.py tests/__init__.py .gitignore
git commit -m "feat: project scaffold"
```

---

## Task 2: runner.py — Shell Executor

**Files:**
- Create: `setup_vps/runner.py`
- Create: `tests/test_runner.py`
- Create: `logs/.gitkeep`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner.py -v
```

Expected: `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Implement runner.py**

```python
# setup_vps/runner.py
import subprocess
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Optional, Union


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    cmd: str


def run_cmd(
    cmd: Union[list[str], str],
    capture: bool = True,
    log_path: Optional[Path] = None,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    input: Optional[str] = None,
) -> CommandResult:
    """Run a shell command, optionally log output to file."""
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
        cmd_str = cmd
    else:
        cmd_list = cmd
        cmd_str = shlex.join(cmd)

    result = subprocess.run(
        cmd_list,
        capture_output=capture,
        text=True,
        env=env,
        cwd=cwd,
        input=input,
    )

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(f"\n[{datetime.now().isoformat()}] $ {cmd_str}\n")
            if stdout:
                f.write(stdout)
            if stderr:
                f.write(stderr)
            f.write(f"[exit: {result.returncode}]\n")

    return CommandResult(
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
        cmd=cmd_str,
    )


def run_shell(cmd: str, **kwargs) -> CommandResult:
    """Run a shell string via bash -c."""
    return run_cmd(["bash", "-c", cmd], **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_runner.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add setup_vps/runner.py tests/test_runner.py
git commit -m "feat: shell runner with logging"
```

---

## Task 3: state.py — Checkpoint State

**Files:**
- Create: `setup_vps/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_state.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement state.py**

```python
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
            self._data = json.loads(self.path.read_text())
        # ensure all steps present
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_state.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add setup_vps/state.py tests/test_state.py
git commit -m "feat: checkpoint state management"
```

---

## Task 4: config.py — Config Load/Save/Wizard

**Files:**
- Create: `setup_vps/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import pytest
import yaml
from pathlib import Path
from setup_vps.config import Config, load_config, save_config, config_hash, generate_secrets


def test_load_config_missing_returns_none(tmp_path):
    assert load_config(tmp_path / "config.yaml") is None


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.main_domain == "main.example.com"
    assert loaded.cdn_domain == "cdn.example.com"


def test_config_hash_deterministic(tmp_path):
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    h1 = config_hash(cfg)
    h2 = config_hash(cfg)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_config_hash_changes_on_mutation(tmp_path):
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    h1 = config_hash(cfg)
    cfg.main_domain = "other.example.com"
    h2 = config_hash(cfg)
    assert h1 != h2


def test_generate_secrets_fills_empty_fields():
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    assert cfg.xray_uuid == ""
    generate_secrets(cfg)
    assert cfg.xray_uuid != ""
    assert cfg.hysteria2_auth_password != ""
    assert cfg.hysteria2_salamander_password != ""
    assert len(cfg.ssh_knock_ports) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement config.py**

```python
# setup_vps/config.py
import hashlib
import random
import secrets
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Config:
    main_domain: str = ""
    cdn_domain: str = ""
    server_ip: str = ""
    email: str = ""

    # SSH / knock
    ssh_knock_ports: list[int] = field(default_factory=list)

    # Xray
    xray_uuid: str = ""
    xray_reality_private_key: str = ""
    xray_reality_public_key: str = ""
    xray_reality_short_id: str = ""
    xray_reality_target: str = "www.microsoft.com:443"
    xray_reality_server_name: str = "www.microsoft.com"

    # Hysteria2
    hysteria2_auth_password: str = ""
    hysteria2_salamander_password: str = ""


def load_config(path: Path) -> Optional[Config]:
    path = Path(path)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def save_config(cfg: Config, path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(asdict(cfg), default_flow_style=False, allow_unicode=True))


def config_hash(cfg: Config) -> str:
    data = yaml.dump(asdict(cfg), default_flow_style=False, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def generate_secrets(cfg: Config):
    """Fill empty auto-generated fields in place. Does NOT overwrite existing values."""
    if not cfg.xray_uuid:
        cfg.xray_uuid = str(uuid.uuid4())
    if not cfg.ssh_knock_ports:
        ports = random.sample(range(5000, 65000), 3)
        cfg.ssh_knock_ports = sorted(ports)
    if not cfg.hysteria2_auth_password:
        cfg.hysteria2_auth_password = secrets.token_urlsafe(24)
    if not cfg.hysteria2_salamander_password:
        cfg.hysteria2_salamander_password = secrets.token_urlsafe(24)
    # xray keys generated after xray binary installed — done in s07
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add setup_vps/config.py tests/test_config.py
git commit -m "feat: config load/save/hash/secrets"
```

---

## Task 5: steps/base.py — BaseStep Interface

**Files:**
- Create: `setup_vps/steps/__init__.py`
- Create: `setup_vps/steps/base.py`
- Create: `tests/test_steps_base.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_steps_base.py
import pytest
from unittest.mock import MagicMock, patch
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.state import StepStatus


class ConcreteStep(BaseStep):
    name = "test_step"
    title = "Test Step"

    def preflight(self, config, state) -> bool:
        return False  # not done

    def run(self, config, state) -> StepResult:
        return StepResult(success=True, message="ran")

    def verify(self, config, state) -> VerifyResult:
        return VerifyResult(passed=True, checks={"echo": "ok"})


def test_step_result_success():
    r = StepResult(success=True, message="ok")
    assert r.success is True


def test_verify_result_passed():
    r = VerifyResult(passed=True, checks={"check1": "value"})
    assert r.passed is True
    assert r.checks["check1"] == "value"


def test_base_step_has_name():
    step = ConcreteStep()
    assert step.name == "test_step"
    assert step.title == "Test Step"


def test_base_step_preflight_returns_bool():
    step = ConcreteStep()
    result = step.preflight(config=None, state=None)
    assert isinstance(result, bool)


def test_base_step_run_returns_step_result():
    step = ConcreteStep()
    result = step.run(config=None, state=None)
    assert isinstance(result, StepResult)


def test_base_step_verify_returns_verify_result():
    step = ConcreteStep()
    result = step.verify(config=None, state=None)
    assert isinstance(result, VerifyResult)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_steps_base.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement base.py**

```python
# setup_vps/steps/__init__.py
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult

__all__ = ["BaseStep", "StepResult", "VerifyResult"]
```

```python
# setup_vps/steps/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_steps_base.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add setup_vps/steps/ tests/test_steps_base.py
git commit -m "feat: BaseStep interface with StepResult/VerifyResult"
```

---

## Task 6: ui.py — Rich + prompt_toolkit Menus

**Files:**
- Create: `setup_vps/ui.py`

- [ ] **Step 1: Implement ui.py**

No unit tests for UI rendering — tested manually at end.

```python
# setup_vps/ui.py
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich import box
from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from typing import Callable, Optional

from setup_vps.state import StepStatus

console = Console()

STATUS_ICONS = {
    StepStatus.DONE: "[green]✓[/green]",
    StepStatus.FAILED: "[red]✗[/red]",
    StepStatus.PENDING: "[dim]●[/dim]",
    StepStatus.STALE: "[yellow]⚠[/yellow]",
    StepStatus.RUNNING: "[cyan]⟳[/cyan]",
    StepStatus.SKIPPED: "[dim]–[/dim]",
}

PT_STYLE = Style.from_dict({
    "prompt": "ansicyan bold",
    "": "ansiwhite",
})


def print_main_menu(domain: str, steps: list[tuple[str, str, StepStatus]], done: int, total: int):
    table = Table(box=box.DOUBLE_EDGE, show_header=False, border_style="cyan", expand=False)
    table.add_column(width=36)
    table.add_column(width=10, justify="right")

    for idx, (name, title, status) in enumerate(steps, 1):
        icon = STATUS_ICONS.get(status, "?")
        table.add_row(f"  [{idx}] {title}", f"{icon} {status.value}  ")

    table.add_row("", "")
    table.add_row("  [r] Run all pending   [c] Config", "")
    table.add_row("  [v] Verify step       [q] Quit", "")

    panel = Panel(
        table,
        title=f"[bold cyan] VPS Setup — {domain} [/bold cyan]",
        subtitle=f"[dim]{done}/{total} complete[/dim]",
        border_style="cyan",
    )
    console.print(panel)


def ask_main_choice() -> str:
    return prompt(
        HTML("<ansicyan><b>choice</b></ansicyan> > "),
        style=PT_STYLE,
    ).strip().lower()


def ask_step_choice(step_title: str) -> str:
    console.print(f"\n[bold]{step_title}[/bold]")
    console.print("  [r] Run    [v] Verify only    [s] Show logs    [b] Back\n")
    return prompt(HTML("<ansicyan><b>action</b></ansicyan> > "), style=PT_STYLE).strip().lower()


def ask_error_choice() -> str:
    console.print("\n  [r] Retry    [s] Skip    [q] Quit\n")
    return prompt(HTML("<ansicyan><b>choice</b></ansicyan> > "), style=PT_STYLE).strip().lower()


def ask_confirm(message: str) -> bool:
    result = prompt(HTML(f"<ansiyellow><b>{message} [yes/no]</b></ansiyellow> > "), style=PT_STYLE).strip().lower()
    return result in ("yes", "y")


def print_check_result(label: str, value: str, passed: bool):
    icon = "[green]✓[/green]" if passed else "[red]✗[/red]"
    console.print(f"  {icon} {label}: [dim]{value}[/dim]")


def print_step_header(title: str):
    console.rule(f"[bold cyan]{title}[/bold cyan]")


def print_success(msg: str):
    console.print(f"[green]✓[/green] {msg}")


def print_error(msg: str):
    console.print(f"[red]✗[/red] {msg}")


def print_warning(msg: str):
    console.print(f"[yellow]⚠[/yellow]  {msg}")


def print_info(msg: str):
    console.print(f"[dim]→[/dim] {msg}")


def print_box(title: str, content: str, style: str = "cyan"):
    console.print(Panel(content, title=title, border_style=style))
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/ui.py
git commit -m "feat: Rich + prompt_toolkit UI helpers"
```

---

## Task 7: s01_system.py — System Preparation

**Files:**
- Create: `setup_vps/steps/s01_system.py`

- [ ] **Step 1: Implement s01_system.py**

```python
# setup_vps/steps/s01_system.py
import re
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell, run_cmd
from setup_vps.ui import print_info, print_success, print_error


SWAP_FILE = "/swapfile"
SWAP_SIZE_MB = 2048
LIMITS_CONF = "/etc/security/limits.conf"
SYSTEMD_CONF = "/etc/systemd/system.conf"
SYSCONF_LIMITS_MARKER = "# setup-vps: ulimits"

REQUIRED_PACKAGES = [
    "curl", "wget", "gnupg", "ca-certificates",
    "unattended-upgrades", "apt-listchanges",
    "ufw", "knockd", "certbot", "nginx",
    "net-tools", "lsof",
]


class SystemPreparationStep(BaseStep):
    name = "s01_system"
    title = "System Preparation"
    description = "apt upgrade, swap 2GB, ulimits, hostname"

    def preflight(self, config, state) -> bool:
        # Check: swap exists AND ulimits set AND hostname correct
        swap = run_shell("swapon --show --bytes", capture=True)
        has_swap = Path(SWAP_FILE).exists() and swap.returncode == 0 and SWAP_FILE in swap.stdout
        limits = Path(LIMITS_CONF).read_text() if Path(LIMITS_CONF).exists() else ""
        has_limits = SYSCONF_LIMITS_MARKER in limits
        hostname = run_shell("hostname -f", capture=True).stdout.strip()
        has_hostname = hostname == config.main_domain if config else False
        return has_swap and has_limits and has_hostname

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        print_info("Updating apt packages...")
        r = run_shell("apt-get update -qq && apt-get full-upgrade -y", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="apt upgrade failed")

        print_info(f"Installing required packages...")
        pkgs = " ".join(REQUIRED_PACKAGES)
        r = run_shell(f"apt-get install -y {pkgs}", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="package install failed")

        print_info("Configuring swap (2GB)...")
        if not Path(SWAP_FILE).exists():
            cmds = [
                f"dd if=/dev/zero of={SWAP_FILE} bs=1M count={SWAP_SIZE_MB} status=none",
                f"chmod 600 {SWAP_FILE}",
                f"mkswap {SWAP_FILE}",
                f"swapon {SWAP_FILE}",
            ]
            for cmd in cmds:
                r = run_shell(cmd, log_path=log)
                if r.returncode != 0:
                    return StepResult(success=False, error=r.stderr, message=f"swap: '{cmd}' failed")
            # persist in fstab
            fstab = Path("/etc/fstab").read_text()
            if SWAP_FILE not in fstab:
                with open("/etc/fstab", "a") as f:
                    f.write(f"\n{SWAP_FILE} none swap sw 0 0\n")

        print_info("Configuring ulimits...")
        limits_content = Path(LIMITS_CONF).read_text() if Path(LIMITS_CONF).exists() else ""
        if SYSCONF_LIMITS_MARKER not in limits_content:
            with open(LIMITS_CONF, "a") as f:
                f.write(f"\n{SYSCONF_LIMITS_MARKER}\n")
                f.write("* soft nofile 1000000\n")
                f.write("* hard nofile 1000000\n")
                f.write("root soft nofile 1000000\n")
                f.write("root hard nofile 1000000\n")

        systemd_conf = Path(SYSTEMD_CONF)
        content = systemd_conf.read_text() if systemd_conf.exists() else ""
        if "DefaultLimitNOFILE" not in content:
            with open(SYSTEMD_CONF, "a") as f:
                f.write("\nDefaultLimitNOFILE=1000000\n")
            run_shell("systemctl daemon-reexec", log_path=log)

        print_info(f"Setting hostname to {config.main_domain}...")
        run_shell(f"hostnamectl set-hostname {config.main_domain}", log_path=log)

        return StepResult(success=True, message="System preparation complete")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        # swap check
        swap = run_shell("free -m", capture=True)
        match = re.search(r"Swap:\s+(\d+)", swap.stdout)
        swap_mb = int(match.group(1)) if match else 0
        checks["swap_mb"] = f"{swap_mb}MB"
        swap_ok = swap_mb >= 1024

        # ulimits check (login shell)
        ulimit = run_shell("bash -l -c 'ulimit -n'", capture=True)
        ulimit_val = ulimit.stdout.strip()
        try:
            ulimit_n = int(ulimit_val)
        except ValueError:
            ulimit_n = 0
        checks["ulimit_nofile"] = ulimit_val
        ulimit_ok = ulimit_n >= 100000  # login shell may not show 1M but /etc/limits will

        # hostname
        hostname = run_shell("hostname -f", capture=True).stdout.strip()
        checks["hostname"] = hostname
        hostname_ok = hostname == config.main_domain

        passed = swap_ok and ulimit_ok and hostname_ok
        return VerifyResult(passed=passed, checks=checks)
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s01_system.py
git commit -m "feat: s01 system preparation step"
```

---

## Task 8: s02_sysctl.py — Kernel Optimization

**Files:**
- Create: `setup_vps/steps/s02_sysctl.py`

- [ ] **Step 1: Implement s02_sysctl.py**

```python
# setup_vps/steps/s02_sysctl.py
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

SYSCTL_FILE = "/etc/sysctl.d/99-server.conf"
SYSCTL_MARKER = "# setup-vps: kernel optimization"

SYSCTL_CONF = """\
# setup-vps: kernel optimization

# ── Congestion Control ──────────────────────────────────────────────────────
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr

# ── TCP Buffers (16 MB max — safe for 2 GB RAM) ────────────────────────────
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_wmem=4096 65536 16777216

# ── TIME_WAIT tuning ────────────────────────────────────────────────────────
net.ipv4.tcp_max_tw_buckets=1440000
net.ipv4.tcp_tw_reuse=1
net.ipv4.ip_local_port_range=1024 65535

# ── Keepalive ───────────────────────────────────────────────────────────────
net.ipv4.tcp_keepalive_time=60
net.ipv4.tcp_keepalive_intvl=10
net.ipv4.tcp_keepalive_probes=6

# ── SYN flood protection ────────────────────────────────────────────────────
net.ipv4.tcp_syncookies=1
net.ipv4.tcp_syn_retries=2
net.ipv4.tcp_synack_retries=2

# ── File descriptors ────────────────────────────────────────────────────────
fs.file-max=2000000
fs.nr_open=2000000

# ── Swap: only on OOM threat ────────────────────────────────────────────────
vm.swappiness=1
vm.vfs_cache_pressure=50

# ── Spoof protection ────────────────────────────────────────────────────────
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
"""


class SysctlStep(BaseStep):
    name = "s02_sysctl"
    title = "Kernel / Sysctl"
    description = "BBR congestion control, TCP buffer tuning, security hardening"

    def preflight(self, config, state) -> bool:
        if not Path(SYSCTL_FILE).exists():
            return False
        content = Path(SYSCTL_FILE).read_text()
        if SYSCTL_MARKER not in content:
            return False
        bbr = run_shell("sysctl -n net.ipv4.tcp_congestion_control", capture=True)
        return bbr.stdout.strip() == "bbr"

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        print_info("Checking kernel version for BBR support...")
        uname = run_shell("uname -r", capture=True)
        kernel = uname.stdout.strip()
        print_info(f"Kernel: {kernel}")

        print_info("Loading tcp_bbr module...")
        run_shell("modprobe tcp_bbr", log_path=log)

        print_info(f"Writing {SYSCTL_FILE}...")
        Path(SYSCTL_FILE).write_text(SYSCTL_CONF)

        print_info("Applying sysctl settings...")
        r = run_shell("sysctl --system", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="sysctl --system failed")

        return StepResult(success=True, message="Kernel optimization applied")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        bbr = run_shell("sysctl -n net.ipv4.tcp_congestion_control", capture=True).stdout.strip()
        checks["tcp_congestion_control"] = bbr
        bbr_ok = bbr == "bbr"

        qdisc = run_shell("sysctl -n net.core.default_qdisc", capture=True).stdout.strip()
        checks["default_qdisc"] = qdisc
        qdisc_ok = qdisc == "fq"

        swappiness = run_shell("sysctl -n vm.swappiness", capture=True).stdout.strip()
        checks["vm.swappiness"] = swappiness
        swap_ok = swappiness == "1"

        passed = bbr_ok and qdisc_ok and swap_ok
        return VerifyResult(passed=passed, checks=checks)
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s02_sysctl.py
git commit -m "feat: s02 sysctl/BBR optimization step"
```

---

## Task 9: s03_ssh.py — SSH Hardening + Port Knocking

**Files:**
- Create: `setup_vps/steps/s03_ssh.py`

- [ ] **Step 1: Implement s03_ssh.py**

```python
# setup_vps/steps/s03_ssh.py
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_warning, ask_confirm, print_box, console

KNOCKD_CONF = "/etc/knockd.conf"
SSH_HARDENING_CONF = "/etc/ssh/sshd_config.d/99-hardening.conf"
KNOCK_SEQUENCE_FILE = "/root/.knock_sequence"


def _make_knockd_conf(ports: list[int]) -> str:
    p1, p2, p3 = ports
    return f"""\
[options]
    UseSyslog

[openSSH]
    sequence    = {p1},{p2},{p3}
    seq_timeout = 10
    command     = /usr/sbin/ufw allow from %IP% to any port 22 proto tcp comment 'knock-%%IP%%'
    tcpflags    = syn

[closeSSH]
    sequence    = {p3},{p2},{p1}
    seq_timeout = 10
    command     = /usr/sbin/ufw delete allow from %IP% to any port 22 proto tcp
    tcpflags    = syn
"""


SSH_HARDENING = """\
# setup-vps: SSH hardening
PermitRootLogin prohibit-password
PasswordAuthentication no
PermitEmptyPasswords no
ChallengeResponseAuthentication no
X11Forwarding no
AllowTcpForwarding no
MaxAuthTries 3
LoginGraceTime 30
"""


class SSHHardeningStep(BaseStep):
    name = "s03_ssh"
    title = "SSH + Port Knocking"
    description = "SSH key-only auth, knockd port knocking"

    def preflight(self, config, state) -> bool:
        knockd_ok = Path(KNOCKD_CONF).exists()
        hardening_ok = Path(SSH_HARDENING_CONF).exists()
        knockd_active = run_shell("systemctl is-active knockd", capture=True).stdout.strip() == "active"
        return knockd_ok and hardening_ok and knockd_active

    def run(self, config, state) -> StepResult:
        log = self.log_path()
        ports = config.ssh_knock_ports

        # Safety gate — show knock sequence before locking SSH
        knock_str = " → ".join(str(p) for p in ports)
        print_box(
            "[bold red]⚠ SECURITY WARNING[/bold red]",
            f"[bold]Port knocking sequence:[/bold]\n\n"
            f"  [cyan]{knock_str}[/cyan]\n\n"
            f"[bold]Save this now.[/bold] After SSH hardening, you MUST knock before connecting:\n\n"
            f"  knock SERVER_IP {' '.join(str(p) for p in ports)} -d 150\n\n"
            f"Also saved to: [dim]{KNOCK_SEQUENCE_FILE}[/dim]",
            style="red",
        )

        if not ask_confirm("I have saved the knock sequence and understand SSH will require knocking"):
            return StepResult(success=False, message="Aborted by user")

        # Save knock sequence
        Path(KNOCK_SEQUENCE_FILE).write_text(f"{' '.join(str(p) for p in ports)}\n")
        Path(KNOCK_SEQUENCE_FILE).chmod(0o600)

        print_info("Writing knockd config...")
        Path(KNOCKD_CONF).write_text(_make_knockd_conf(ports))

        print_info("Enabling knockd...")
        r = run_shell("systemctl enable --now knockd", log_path=log)
        if r.returncode != 0:
            # Debian: knockd needs /etc/default/knockd ENABLED=1
            default = Path("/etc/default/knockd")
            if default.exists():
                content = default.read_text()
                content = content.replace('START_KNOCKD=0', 'START_KNOCKD=1')
                default.write_text(content)
            run_shell("systemctl enable --now knockd", log_path=log)

        print_info("Writing SSH hardening config...")
        Path(SSH_HARDENING_CONF).parent.mkdir(parents=True, exist_ok=True)
        Path(SSH_HARDENING_CONF).write_text(SSH_HARDENING)

        print_info("Testing sshd config...")
        r = run_shell("sshd -t", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="sshd config test failed")

        print_info("Reloading sshd...")
        run_shell("systemctl reload sshd || systemctl reload ssh", log_path=log)

        return StepResult(success=True, message="SSH hardening + knockd configured")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        passwd_auth = run_shell("sshd -T | grep -i passwordauthentication", capture=True).stdout.strip()
        checks["passwordauthentication"] = passwd_auth
        passwd_ok = "no" in passwd_auth.lower()

        knockd = run_shell("systemctl is-active knockd", capture=True).stdout.strip()
        checks["knockd_active"] = knockd
        knockd_ok = knockd == "active"

        passed = passwd_ok and knockd_ok
        return VerifyResult(passed=passed, checks=checks)
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s03_ssh.py
git commit -m "feat: s03 SSH hardening + port knocking step"
```

---

## Task 10: s04_firewall.py — UFW Rules

**Files:**
- Create: `setup_vps/steps/s04_firewall.py`

- [ ] **Step 1: Implement s04_firewall.py**

```python
# setup_vps/steps/s04_firewall.py
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

REQUIRED_RULES = [
    ("80", "tcp"),
    ("443", "tcp"),
    ("443", "udp"),
]


class FirewallStep(BaseStep):
    name = "s04_firewall"
    title = "Firewall (UFW)"
    description = "UFW: allow 80/tcp, 443/tcp+udp, 20000-50000/udp; SSH via knockd only"

    def preflight(self, config, state) -> bool:
        status = run_shell("ufw status", capture=True)
        if "Status: active" not in status.stdout:
            return False
        for port, proto in REQUIRED_RULES:
            rule = f"{port}/{proto}"
            if rule not in status.stdout:
                return False
        return True

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        print_info("Resetting UFW...")
        run_shell("ufw --force reset", log_path=log)
        run_shell("ufw default deny incoming", log_path=log)
        run_shell("ufw default allow outgoing", log_path=log)

        rules = [
            ("80/tcp",           "HTTP: certbot + redirect"),
            ("443/tcp",          "HTTPS: VPN traffic"),
            ("443/udp",          "Hysteria2 QUIC"),
            ("20000:50000/udp",  "Hysteria2 port hopping"),
        ]

        for rule, comment in rules:
            print_info(f"Opening {rule} ({comment})...")
            r = run_shell(f"ufw allow {rule} comment '{comment}'", log_path=log)
            if r.returncode != 0:
                return StepResult(success=False, error=r.stderr, message=f"ufw allow {rule} failed")

        print_info("Enabling UFW...")
        r = run_shell("ufw --force enable", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="ufw enable failed")

        return StepResult(success=True, message="UFW configured")

    def verify(self, config, state) -> VerifyResult:
        status = run_shell("ufw status verbose", capture=True).stdout
        checks = {"ufw_status": "active" if "Status: active" in status else "inactive"}

        rules_ok = True
        for port, proto in REQUIRED_RULES:
            key = f"{port}/{proto}"
            present = key in status
            checks[key] = "present" if present else "MISSING"
            if not present:
                rules_ok = False

        # Check hopping range
        hopping = "20000:50000/udp" in status
        checks["20000:50000/udp"] = "present" if hopping else "MISSING"
        if not hopping:
            rules_ok = False

        passed = "Status: active" in status and rules_ok
        return VerifyResult(passed=passed, checks=checks)
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s04_firewall.py
git commit -m "feat: s04 UFW firewall step"
```

---

## Task 11: s05_certificates.py — SSL Certificates

**Files:**
- Create: `setup_vps/steps/s05_certificates.py`

- [ ] **Step 1: Implement s05_certificates.py**

```python
# setup_vps/steps/s05_certificates.py
import re
from datetime import datetime
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_warning


def _cert_path(domain: str) -> Path:
    return Path(f"/etc/letsencrypt/live/{domain}/fullchain.pem")


def _cert_expiry_days(domain: str) -> int:
    result = run_shell(
        f"openssl x509 -enddate -noout -in /etc/letsencrypt/live/{domain}/fullchain.pem",
        capture=True,
    )
    match = re.search(r"notAfter=(.*)", result.stdout)
    if not match:
        return -1
    try:
        expiry = datetime.strptime(match.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
        return (expiry - datetime.utcnow()).days
    except Exception:
        return -1


class CertificatesStep(BaseStep):
    name = "s05_certificates"
    title = "SSL Certificates"
    description = "certbot standalone certificates for main + cdn domains"

    def preflight(self, config, state) -> bool:
        main_ok = _cert_path(config.main_domain).exists() and _cert_expiry_days(config.main_domain) > 30
        cdn_ok = _cert_path(config.cdn_domain).exists() and _cert_expiry_days(config.cdn_domain) > 30
        return main_ok and cdn_ok

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        # Stop nginx if running (port 80 needed for standalone)
        print_info("Stopping nginx temporarily (port 80 needed)...")
        run_shell("systemctl stop nginx 2>/dev/null || true", log_path=log)

        for domain in [config.main_domain, config.cdn_domain]:
            if _cert_path(domain).exists() and _cert_expiry_days(domain) > 30:
                print_info(f"Certificate for {domain} already valid, skipping...")
                continue

            print_info(f"Obtaining certificate for {domain}...")
            r = run_shell(
                f"certbot certonly --standalone --non-interactive --agree-tos "
                f"--email {config.email} -d {domain}",
                log_path=log,
            )
            if r.returncode != 0:
                run_shell("systemctl start nginx 2>/dev/null || true", log_path=log)
                return StepResult(success=False, error=r.stderr, message=f"certbot failed for {domain}")

        print_info("Restarting nginx...")
        run_shell("systemctl start nginx 2>/dev/null || true", log_path=log)

        print_info("Verifying certbot renewal timer...")
        run_shell("systemctl enable --now certbot.timer 2>/dev/null || true", log_path=log)

        return StepResult(success=True, message="SSL certificates obtained")

    def verify(self, config, state) -> VerifyResult:
        checks = {}
        passed = True

        for domain in [config.main_domain, config.cdn_domain]:
            exists = _cert_path(domain).exists()
            days = _cert_expiry_days(domain) if exists else -1
            checks[f"{domain}_exists"] = str(exists)
            checks[f"{domain}_expiry_days"] = str(days)
            if not exists or days < 30:
                passed = False

        return VerifyResult(passed=passed, checks=checks)
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s05_certificates.py
git commit -m "feat: s05 SSL certificates step"
```

---

## Task 12: s06_nginx.py — Nginx Configuration

**Files:**
- Create: `setup_vps/steps/s06_nginx.py`

- [ ] **Step 1: Implement s06_nginx.py**

```python
# setup_vps/steps/s06_nginx.py
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

NGINX_STREAM_CONF = "/etc/nginx/conf.d/stream.conf"
NGINX_HTTP_CONF = "/etc/nginx/sites-available/vpn"
NGINX_MARKER = "# setup-vps: nginx"


def _stream_conf(main_domain: str, cdn_domain: str) -> str:
    return f"""\
{NGINX_MARKER}
stream {{
    log_format basic '$remote_addr [$time_local] $protocol $status $bytes_sent $bytes_received $session_time';

    map $ssl_preread_server_name $backend {{
        {main_domain}  127.0.0.1:8443;
        {cdn_domain}   127.0.0.1:8443;
        default        127.0.0.1:8443;
    }}

    server {{
        listen 443;
        listen [::]:443;
        ssl_preread on;
        proxy_pass $backend;
        proxy_protocol off;
    }}
}}
"""


def _http_conf(main_domain: str, cdn_domain: str) -> str:
    return f"""\
{NGINX_MARKER}
# Redirect HTTP → HTTPS
server {{
    listen 80;
    listen [::]:80;
    server_name {main_domain} {cdn_domain} _;
    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

# Internal HTTPS server (reached via stream proxy)
server {{
    listen 127.0.0.1:8443 ssl;
    http2 on;
    server_name {main_domain} {cdn_domain} _;

    ssl_certificate     /etc/letsencrypt/live/{main_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{main_domain}/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/{main_domain}/chain.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL_8443:10m;
    ssl_session_timeout 1d;

    # XHTTP inbound proxy (cdn domain → xray)
    location /api/v1/sync {{
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
    }}

    location / {{
        return 404;
    }}
}}
"""


class NginxStep(BaseStep):
    name = "s06_nginx"
    title = "Nginx"
    description = "nginx stream (SNI routing) + HTTPS vhosts"

    def preflight(self, config, state) -> bool:
        if not Path(NGINX_STREAM_CONF).exists():
            return False
        content = Path(NGINX_STREAM_CONF).read_text()
        if NGINX_MARKER not in content:
            return False
        test = run_shell("nginx -t", capture=True)
        active = run_shell("systemctl is-active nginx", capture=True).stdout.strip()
        return test.returncode == 0 and active == "active"

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        print_info("Installing nginx...")
        r = run_shell("apt-get install -y nginx", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx install failed")

        # Enable stream module
        print_info("Configuring nginx stream (SNI routing)...")

        # Ensure stream module enabled in nginx.conf
        nginx_conf = Path("/etc/nginx/nginx.conf")
        content = nginx_conf.read_text()
        if "stream_module" not in content and "include /etc/nginx/conf.d/stream.conf" not in content:
            # Write stream conf separately — it uses top-level stream{} block
            pass  # stream{} block goes into conf.d/stream.conf directly

        Path(NGINX_STREAM_CONF).write_text(_stream_conf(config.main_domain, config.cdn_domain))

        print_info("Configuring HTTP vhosts...")
        Path(NGINX_HTTP_CONF).write_text(_http_conf(config.main_domain, config.cdn_domain))
        symlink = Path("/etc/nginx/sites-enabled/vpn")
        if not symlink.exists():
            symlink.symlink_to(NGINX_HTTP_CONF)

        # Remove default site
        default = Path("/etc/nginx/sites-enabled/default")
        if default.exists():
            default.unlink()

        print_info("Testing nginx config...")
        r = run_shell("nginx -t", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx -t failed")

        print_info("Restarting nginx...")
        r = run_shell("systemctl enable --now nginx && systemctl restart nginx", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx restart failed")

        return StepResult(success=True, message="Nginx configured")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        test = run_shell("nginx -t 2>&1", capture=True)
        checks["nginx_t"] = "ok" if test.returncode == 0 else test.stdout.strip()
        test_ok = test.returncode == 0

        active = run_shell("systemctl is-active nginx", capture=True).stdout.strip()
        checks["nginx_active"] = active
        active_ok = active == "active"

        # HTTP redirect check
        http_probe = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1 -H 'Host: {config.main_domain}' --max-time 5",
            capture=True,
        )
        checks["http_redirect"] = http_probe.stdout.strip()
        http_ok = http_probe.stdout.strip() in ("301", "302", "200")

        passed = test_ok and active_ok and http_ok
        return VerifyResult(passed=passed, checks=checks)
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s06_nginx.py
git commit -m "feat: s06 nginx stream + vhosts step"
```

---

## Task 13: s07_xray.py — Xray Installation + Config

**Files:**
- Create: `setup_vps/steps/s07_xray.py`

- [ ] **Step 1: Implement s07_xray.py**

```python
# setup_vps/steps/s07_xray.py
import json
import re
import secrets
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_box, console
from setup_vps.config import save_config

XRAY_CONF = "/usr/local/etc/xray/config.json"
XRAY_BIN = "/usr/local/bin/xray"


def _xray_config(cfg) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "vless-reality",
                "listen": "0.0.0.0",
                "port": 1443,
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": cfg.xray_uuid, "flow": "xtls-rprx-vision"}],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": cfg.xray_reality_target,
                        "xver": 0,
                        "serverNames": [cfg.xray_reality_server_name],
                        "privateKey": cfg.xray_reality_private_key,
                        "shortIds": [cfg.xray_reality_short_id],
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "tag": "vless-xhttp",
                "listen": "127.0.0.1",
                "port": 8001,
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": cfg.xray_uuid}],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "xhttp",
                    "xhttpSettings": {
                        "path": "/api/v1/sync",
                        "host": cfg.cdn_domain,
                        "mode": "stream-one",
                    },
                },
            },
            {
                "tag": "hysteria2",
                "listen": "0.0.0.0",
                "port": 443,
                "protocol": "hysteria2",
                "settings": {
                    "password": cfg.hysteria2_auth_password,
                    "salamander": {"password": cfg.hysteria2_salamander_password},
                },
                "streamSettings": {
                    "network": "udp",
                    "security": "tls",
                    "tlsSettings": {
                        "certificates": [{
                            "certificateFile": f"/etc/letsencrypt/live/{cfg.main_domain}/fullchain.pem",
                            "keyFile": f"/etc/letsencrypt/live/{cfg.main_domain}/privkey.pem",
                        }],
                    },
                },
            },
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "blocked"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
            ]
        },
    }


class XrayStep(BaseStep):
    name = "s07_xray"
    title = "Xray"
    description = "xray-core install, key generation, VLESS+REALITY+XHTTP+Hysteria2 config"

    def preflight(self, config, state) -> bool:
        if not Path(XRAY_BIN).exists():
            return False
        if not Path(XRAY_CONF).exists():
            return False
        active = run_shell("systemctl is-active xray", capture=True).stdout.strip()
        return active == "active"

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        if not Path(XRAY_BIN).exists():
            print_info("Installing xray-core...")
            r = run_shell(
                "bash -c \"$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install",
                log_path=log,
            )
            if r.returncode != 0 or not Path(XRAY_BIN).exists():
                return StepResult(success=False, error=r.stderr, message="xray install failed")

        # Generate keys if not set
        if not config.xray_reality_private_key:
            print_info("Generating x25519 keypair...")
            r = run_shell(f"{XRAY_BIN} x25519", capture=True)
            if r.returncode != 0:
                return StepResult(success=False, error=r.stderr, message="xray x25519 keygen failed")
            priv_match = re.search(r"Private key: (.+)", r.stdout)
            pub_match = re.search(r"Public key: (.+)", r.stdout)
            if priv_match and pub_match:
                config.xray_reality_private_key = priv_match.group(1).strip()
                config.xray_reality_public_key = pub_match.group(1).strip()

        if not config.xray_reality_short_id:
            config.xray_reality_short_id = secrets.token_hex(8)

        # Save updated config with generated keys
        save_config(config, Path("config.yaml"))

        print_info("Writing xray config.json...")
        Path(XRAY_CONF).parent.mkdir(parents=True, exist_ok=True)
        Path(XRAY_CONF).write_text(json.dumps(_xray_config(config), indent=2))

        print_info("Testing xray config...")
        r = run_shell(f"{XRAY_BIN} run -test -config {XRAY_CONF}", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="xray config test failed")

        print_info("Enabling xray service...")
        r = run_shell("systemctl enable --now xray", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="xray start failed")

        # Show client config summary
        print_box(
            "[bold cyan]Xray Client Configuration[/bold cyan]",
            f"[bold]VLESS TCP REALITY:[/bold]\n"
            f"  Address:    [cyan]{config.main_domain}[/cyan]\n"
            f"  Port:       1443\n"
            f"  UUID:       [cyan]{config.xray_uuid}[/cyan]\n"
            f"  Flow:       xtls-rprx-vision\n"
            f"  PublicKey:  [cyan]{config.xray_reality_public_key}[/cyan]\n"
            f"  ShortId:    [cyan]{config.xray_reality_short_id}[/cyan]\n"
            f"  SNI:        {config.xray_reality_server_name}\n\n"
            f"[bold]VLESS XHTTP (CDN):[/bold]\n"
            f"  Address:    [cyan]{config.cdn_domain}[/cyan]\n"
            f"  Port:       443, Path: /api/v1/sync\n"
            f"  UUID:       [cyan]{config.xray_uuid}[/cyan]\n\n"
            f"[bold]Hysteria2:[/bold]\n"
            f"  Address:    [cyan]{config.main_domain}[/cyan]:443\n"
            f"  Auth:       [cyan]{config.hysteria2_auth_password}[/cyan]\n"
            f"  Salamander: [cyan]{config.hysteria2_salamander_password}[/cyan]\n\n"
            f"[dim]All keys also saved in config.yaml[/dim]",
            style="cyan",
        )

        return StepResult(success=True, message="Xray configured. See client config above.")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        test = run_shell(f"{XRAY_BIN} run -test -config {XRAY_CONF} 2>&1", capture=True)
        checks["xray_config_test"] = "ok" if test.returncode == 0 else test.stdout.strip()[:80]
        test_ok = test.returncode == 0

        active = run_shell("systemctl is-active xray", capture=True).stdout.strip()
        checks["xray_active"] = active
        active_ok = active == "active"

        passed = test_ok and active_ok
        return VerifyResult(passed=passed, checks=checks)
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s07_xray.py
git commit -m "feat: s07 xray install + VLESS/XHTTP/Hysteria2 config"
```

---

## Task 14: s08_verify.py — Final Verification

**Files:**
- Create: `setup_vps/steps/s08_verify.py`

- [ ] **Step 1: Implement s08_verify.py**

```python
# setup_vps/steps/s08_verify.py
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.steps.s01_system import SystemPreparationStep
from setup_vps.steps.s02_sysctl import SysctlStep
from setup_vps.steps.s03_ssh import SSHHardeningStep
from setup_vps.steps.s04_firewall import FirewallStep
from setup_vps.steps.s05_certificates import CertificatesStep
from setup_vps.steps.s06_nginx import NginxStep
from setup_vps.steps.s07_xray import XrayStep
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_check_result, console


ALL_STEPS = [
    SystemPreparationStep(),
    SysctlStep(),
    SSHHardeningStep(),
    FirewallStep(),
    CertificatesStep(),
    NginxStep(),
    XrayStep(),
]


class FinalVerificationStep(BaseStep):
    name = "s08_verify"
    title = "Final Verification"
    description = "Re-verify all steps + connectivity checks"

    def preflight(self, config, state) -> bool:
        return False  # always run

    def run(self, config, state) -> StepResult:
        all_passed = True
        for step in ALL_STEPS:
            print_info(f"Verifying: {step.title}...")
            result = step.verify(config, state)
            for label, value in result.checks.items():
                passed = "missing" not in value.lower() and "failed" not in value.lower() and value not in ("inactive", "false", "0")
                print_check_result(label, value, passed=result.passed)
            if not result.passed:
                all_passed = False

        # Additional connectivity checks
        print_info("Checking XHTTP endpoint...")
        xhttp = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"https://{config.cdn_domain}/api/v1/sync "
            f"--max-time 5 -k",
            capture=True,
        )
        code = xhttp.stdout.strip()
        print_check_result("xhttp_endpoint", code, passed=code in ("200", "400", "405"))

        if all_passed:
            return StepResult(success=True, message="All verification checks passed")
        else:
            return StepResult(success=False, message="Some checks failed — see output above")

    def verify(self, config, state) -> VerifyResult:
        return VerifyResult(passed=True, checks={"note": "run step for full verification"})
```

- [ ] **Step 2: Commit**

```bash
git add setup_vps/steps/s08_verify.py
git commit -m "feat: s08 final verification step"
```

---

## Task 15: main.py — CLI Entry + Main Menu

**Files:**
- Create: `setup_vps/main.py`

- [ ] **Step 1: Implement main.py**

```python
# setup_vps/main.py
import sys
from pathlib import Path

import click

from setup_vps.config import Config, load_config, save_config, config_hash, generate_secrets
from setup_vps.state import State, StepStatus
from setup_vps.ui import (
    console, print_main_menu, ask_main_choice, ask_step_choice,
    ask_error_choice, ask_confirm, print_step_header, print_success,
    print_error, print_warning, print_info,
)
from setup_vps.steps.s01_system import SystemPreparationStep
from setup_vps.steps.s02_sysctl import SysctlStep
from setup_vps.steps.s03_ssh import SSHHardeningStep
from setup_vps.steps.s04_firewall import FirewallStep
from setup_vps.steps.s05_certificates import CertificatesStep
from setup_vps.steps.s06_nginx import NginxStep
from setup_vps.steps.s07_xray import XrayStep
from setup_vps.steps.s08_verify import FinalVerificationStep

CONFIG_PATH = Path("config.yaml")
STATE_PATH = Path("state.json")

STEPS = [
    SystemPreparationStep(),
    SysctlStep(),
    SSHHardeningStep(),
    FirewallStep(),
    CertificatesStep(),
    NginxStep(),
    XrayStep(),
    FinalVerificationStep(),
]

STEP_NAMES = [s.name for s in STEPS]


def _run_wizard() -> Config:
    from prompt_toolkit import prompt
    from prompt_toolkit.styles import Style

    style = Style.from_dict({"prompt": "ansicyan bold"})
    console.print("\n[bold cyan]VPS Setup — Configuration Wizard[/bold cyan]\n")

    def ask(label: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        return prompt(f"  {label}{hint}: ", style=style).strip() or default

    cfg = Config(
        main_domain=ask("Main domain (e.g. main.example.com)"),
        cdn_domain=ask("CDN domain (e.g. cdn.example.com)"),
        server_ip=ask("Server public IP"),
        email=ask("Email (for certbot)"),
    )
    generate_secrets(cfg)
    save_config(cfg, CONFIG_PATH)
    print_success(f"Config saved to {CONFIG_PATH}")
    return cfg


def _run_step(step, config, state):
    print_step_header(step.title)

    # Idempotent check
    if step.preflight(config, state) and state.get_status(step.name) == StepStatus.DONE:
        print_info(f"{step.title} already complete, skipping.")
        return True

    state.mark_running(step.name)
    while True:
        try:
            result = step.run(config, state)
        except Exception as e:
            result = None
            print_error(f"Exception: {e}")

        if result and result.success:
            # Verify
            verify = step.verify(config, state)
            if verify.passed:
                state.mark_done(step.name)
                print_success(f"{step.title} complete.")
                return True
            else:
                print_error(f"Verification failed: {verify.checks}")
                choice = ask_error_choice()
                if choice == "r":
                    continue
                elif choice == "s":
                    state.mark_pending(step.name)
                    return False
                else:
                    state.mark_failed(step.name, error="verification failed")
                    sys.exit(1)
        else:
            error = result.error if result else str(e)
            print_error(f"Step failed: {result.message if result else 'exception'}")
            if error:
                console.print(f"[dim]{error[:500]}[/dim]")
            choice = ask_error_choice()
            if choice == "r":
                continue
            elif choice == "s":
                state.mark_pending(step.name)
                return False
            else:
                state.mark_failed(step.name, error=error[:200])
                sys.exit(1)


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Config file path")
@click.option("--state", "state_path", default="state.json", help="State file path")
def cli(config_path, state_path):
    """VPS Setup CLI — VLESS TCP REALITY + XHTTP + Hysteria2"""
    global CONFIG_PATH, STATE_PATH
    CONFIG_PATH = Path(config_path)
    STATE_PATH = Path(state_path)

    console.print("\n[bold cyan]═══ VPS Setup ═══[/bold cyan]\n")

    # Check root
    import os
    if os.geteuid() != 0:
        print_error("Must run as root (sudo).")
        sys.exit(1)

    # Load or create config
    config = load_config(CONFIG_PATH)
    if config is None:
        config = _run_wizard()

    state = State(path=STATE_PATH, step_names=STEP_NAMES)

    # Check for config changes
    chash = config_hash(config)
    if not state.is_config_changed(chash):
        pass  # no change
    else:
        state.set_config_hash(chash)

    # Main menu loop
    while True:
        console.clear()
        step_list = [(s.name, s.title, state.get_status(s.name)) for s in STEPS]
        done = state.done_count()
        print_main_menu(config.main_domain or "unconfigured", step_list, done, len(STEPS))

        choice = ask_main_choice()

        if choice == "q":
            break
        elif choice == "r":
            for step in STEPS:
                status = state.get_status(step.name)
                if status not in (StepStatus.DONE,):
                    ok = _run_step(step, config, state)
                    if not ok and not ask_confirm("Continue with next step?"):
                        break
        elif choice == "c":
            config = _run_wizard()
            state = State(path=STATE_PATH, step_names=STEP_NAMES)
        elif choice == "v":
            idx_str = ask_main_choice()
            try:
                idx = int(idx_str) - 1
                step = STEPS[idx]
                result = step.verify(config, state)
                for label, value in result.checks.items():
                    ok = "missing" not in value.lower() and value not in ("inactive",)
                    print_info(f"  {label}: {value}")
            except (ValueError, IndexError):
                print_error("Invalid step number")
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(STEPS):
                step = STEPS[idx]
                action = ask_step_choice(step.title)
                if action == "r":
                    _run_step(step, config, state)
                elif action == "v":
                    result = step.verify(config, state)
                    for label, value in result.checks.items():
                        print_info(f"  {label}: {value}")
                elif action == "s":
                    log = step.log_path()
                    if log.exists():
                        console.print(log.read_text()[-3000:])
                    else:
                        print_info("No log file yet.")
        else:
            print_warning("Unknown command")


if __name__ == "__main__":
    cli()
```

- [ ] **Step 2: Install and smoke test**

```bash
cd /home/admin/PycharmProjects/setup-vps
source .venv/bin/activate
pip install -e .
setup-vps --help
```

Expected: help text with options shown.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add setup_vps/main.py
git commit -m "feat: main CLI entry + interactive menu"
```

---

## Task 16: Self-Review + Final Checks

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v --tb=short
```

Expected: all PASS.

- [ ] **Step 2: Check entry point works**

```bash
setup-vps --help
```

- [ ] **Step 3: Verify step registry in steps/__init__.py**

```python
# setup_vps/steps/__init__.py  — final version
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.steps.s01_system import SystemPreparationStep
from setup_vps.steps.s02_sysctl import SysctlStep
from setup_vps.steps.s03_ssh import SSHHardeningStep
from setup_vps.steps.s04_firewall import FirewallStep
from setup_vps.steps.s05_certificates import CertificatesStep
from setup_vps.steps.s06_nginx import NginxStep
from setup_vps.steps.s07_xray import XrayStep
from setup_vps.steps.s08_verify import FinalVerificationStep

__all__ = [
    "BaseStep", "StepResult", "VerifyResult",
    "SystemPreparationStep", "SysctlStep", "SSHHardeningStep",
    "FirewallStep", "CertificatesStep", "NginxStep",
    "XrayStep", "FinalVerificationStep",
]
```

- [ ] **Step 4: Final commit**

```bash
git add setup_vps/steps/__init__.py
git commit -m "feat: complete step registry"
git tag v0.1.0
```

---

## End-to-End Verification

```bash
# 1. Fresh install test
source .venv/bin/activate
setup-vps --config /tmp/test-config.yaml --state /tmp/test-state.json
# → wizard prompts → config.yaml created → main menu shows all pending

# 2. Unit tests
pytest tests/ -v
# → all PASS

# 3. State persistence test
# Start run all → Ctrl+C mid-way → restart → completed steps shown as ✓ done

# 4. Verify command
# From menu: [v] → [1] → shows swap/ulimit/hostname checks
```
