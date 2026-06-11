# setup_vps/main.py
import sys
from pathlib import Path

import click

from setup_vps.config import Config, load_config, save_config, config_hash, generate_secrets
from setup_vps.state import State, StepStatus
from setup_vps.ui import (
    console, print_main_menu, ask_main_choice,
    ask_error_choice, ask_confirm, print_step_header, print_success,
    print_error, print_warning, print_info,
)
from prompt_toolkit import prompt
from setup_vps.steps.s00_xanmod import XanModStep
from setup_vps.steps.s01_system import SystemPreparationStep
from setup_vps.steps.s01b_hardware import HardwareTuningStep
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
    XanModStep(),
    SystemPreparationStep(),
    HardwareTuningStep(),
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
        return True, False

    state.mark_running(step.name)
    try:
        result = step.run(config, state)
        if result.success:
            print_success(result.message)
            state.mark_done(step.name)
            return True, result.reboot_required
        else:
            print_error(result.message)
            if result.error:
                from rich.markup import escape
                console.print(escape(str(result.error)), style="dim")
            state.mark_failed(step.name, error=result.error or result.message)
            return False, False
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        state.mark_failed(step.name, error=str(e))
        return False, False


@click.command()
@click.option("--config", "config_file", type=click.Path(), default="config.yaml", help="Path to config file")
def cli(config_file):
    config_path = Path(config_file)
    cfg = load_config(config_path)
    if not cfg:
        if ask_confirm("Config not found. Run wizard?"):
            cfg = _run_wizard()
        else:
            sys.exit(0)

    state = State(STATE_PATH, STEP_NAMES)
    
    # Check for config changes
    current_hash = config_hash(cfg)
    if state.is_config_changed(current_hash):
        print_warning("Config changed since last run. Some steps might need re-running.")
        state.set_config_hash(current_hash)

    while True:
        steps_info = [(s.name, s.title, state.get_status(s.name)) for s in STEPS]
        print_main_menu(cfg.main_domain, steps_info, state.done_count(), len(STEPS))
        
        choice = ask_main_choice()
        
        if choice == "q":
            break
        elif choice == "c":
            cfg = _run_wizard()
            state.set_config_hash(config_hash(cfg))
        elif choice == "r":
            # Run all pending
            for step in STEPS:
                status = state.get_status(step.name)
                if status in (StepStatus.PENDING, StepStatus.FAILED, StepStatus.STALE):
                    success, reboot = _run_step(step, cfg, state)
                    if reboot:
                        print_warning("Reboot required. Please reboot and run again.")
                        break
                    if not success:
                        err_choice = ask_error_choice()
                        if err_choice == "r":
                            # Will retry in next iteration of outer loop if they hit 'r' again
                            # For now, let's just break the 'Run all' sequence
                            break
                        elif err_choice == "s":
                            state.mark_pending(step.name) # or skipped?
                            continue
                        else:
                            break
        elif choice == "v":
            # Verify a step
            idx_str = prompt("  Step number: ").strip()
            try:
                idx = int(idx_str) - 1
                if 0 <= idx < len(STEPS):
                    step = STEPS[idx]
                    print_step_header(f"Verifying {step.title}")
                    res = step.verify(cfg, state)
                    from setup_vps.ui import print_check_result
                    for label, val in res.checks.items():
                        # Simple heuristic
                        passed = "missing" not in val.lower() and "failed" not in val.lower() and val not in ("inactive", "false", "0")
                        print_check_result(label, val, passed=passed)
                    if res.passed:
                        print_success("Verification passed")
                        state.mark_done(step.name)
                    else:
                        print_error("Verification failed")
                else:
                    print_error("Invalid step number")
            except ValueError:
                print_error("Invalid input")
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(STEPS):
                step = STEPS[idx]
                _run_step(step, cfg, state)
            else:
                print_error("Invalid step number")
        else:
            print_error("Invalid choice")


if __name__ == "__main__":
    cli()
