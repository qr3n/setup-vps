# setup_vps/steps/s08_verify.py
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.steps.s01_system import SystemPreparationStep
from setup_vps.steps.s01b_hardware import HardwareTuningStep
from setup_vps.steps.s02_sysctl import SysctlStep
from setup_vps.steps.s03_ssh import SSHHardeningStep
from setup_vps.steps.s04_firewall import FirewallStep
from setup_vps.steps.s05_certificates import CertificatesStep
from setup_vps.steps.s06_nginx import NginxStep
from setup_vps.steps.s07_remnawave import RemnawaveNodeStep
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_check_result, ask_confirm, print_error, print_warning, print_success


ALL_STEPS = [
    SystemPreparationStep(),
    HardwareTuningStep(),
    SysctlStep(),
    SSHHardeningStep(),
    FirewallStep(),
    CertificatesStep(),
    NginxStep(),
    RemnawaveNodeStep(),
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
                # Heuristic for success
                passed = (
                    "missing" not in value.lower() and 
                    "failed" not in value.lower() and 
                    value not in ("inactive", "false", "0", "no")
                )
                print_check_result(label, value, passed=passed)
            if not result.passed:
                all_passed = False

        # 1. Node subdomain check
        print_info(f"Checking Node connectivity via {config.node_domain}...")
        # Since we use self-signed or panel certs, and it's proxied, 
        # we check if we get a response from the node.
        # Remnawave-node usually returns 404 or something if not authenticated, 
        # but it should respond.
        node_check = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"https://{config.node_domain}/ "
            f"--resolve {config.node_domain}:443:127.0.0.1 "
            f"--max-time 5 -k",
            capture=True,
        )
        code = node_check.stdout.strip()
        # Any response from a web server is better than none. 
        # 401/404 are common for an unauthenticated request to an API node.
        node_ok = code in ("200", "401", "404", "405")
        print_check_result("node_domain_local", code or "no response", passed=node_ok)
        
        if not node_ok:
            print_error("Node connectivity check failed.")
            if not ask_confirm("Continue anyway?"):
                return StepResult(success=False, message="Verification failed and user stopped")
            all_passed = False

        if all_passed:
            print_success("\n[bold green]Configuration successfully verified![/bold green]")
            return StepResult(success=True, message="All verification checks passed")
        else:
            return StepResult(success=True, message="Verification finished with some issues")

    def verify(self, config, state) -> VerifyResult:
        return VerifyResult(passed=True, checks={"note": "run step for full verification"})
