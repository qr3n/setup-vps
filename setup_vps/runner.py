# setup_vps/runner.py
import subprocess
import shlex
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Optional, Union, Callable


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
    on_output: Optional[Callable[[str], None]] = None,
) -> CommandResult:
    """Run a shell command, optionally log output to file and stream to callback."""
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
        cmd_str = cmd
    else:
        cmd_list = cmd
        cmd_str = shlex.join(cmd)

    if on_output:
        # Streaming mode
        process = subprocess.Popen(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=cwd,
        )
        stdout_lines = []
        for line in process.stdout:
            on_output(line.rstrip())
            stdout_lines.append(line)
        process.wait()
        result_stdout = "".join(stdout_lines)
        result_returncode = process.returncode
        result_stderr = ""
    else:
        # Standard mode
        result = subprocess.run(
            cmd_list,
            capture_output=capture,
            text=True,
            env=env,
            cwd=cwd,
            input=input,
        )
        result_stdout = result.stdout or ""
        result_stderr = result.stderr or ""
        result_returncode = result.returncode

    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(f"\n[{datetime.now().isoformat()}] $ {cmd_str}\n")
            if result_stdout:
                f.write(result_stdout)
            if result_stderr:
                f.write(result_stderr)
            f.write(f"[exit: {result_returncode}]\n")

    return CommandResult(
        returncode=result_returncode,
        stdout=result_stdout,
        stderr=result_stderr,
        cmd=cmd_str,
    )


def run_shell(cmd: str, **kwargs) -> CommandResult:
    """Run a shell string via bash -c."""
    return run_cmd(["bash", "-c", cmd], **kwargs)
