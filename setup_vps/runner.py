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
