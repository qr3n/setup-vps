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
from setup_vps.ui import print_info, print_check_result


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
                # Heuristic for success
                passed = (
                    "missing" not in value.lower() and 
                    "failed" not in value.lower() and 
                    value not in ("inactive", "false", "0")
                )
                print_check_result(label, value, passed=passed)
            if not result.passed:
                all_passed = False

        # Additional connectivity checks
        print_info("Checking XHTTP endpoint...")
        # Note: we use --resolve to force local connection but still send proper SNI for Nginx stream routing
        xhttp = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"https://{config.cdn_domain}/api/v1/sync "
            f"--resolve {config.cdn_domain}:443:127.0.0.1 "
            f"--max-time 5 -k",
            capture=True,
        )
        code = xhttp.stdout.strip()
        # Xray returns 400 for empty packet-up POST, which is fine
        xhttp_ok = code in ("200", "400", "405")
        print_check_result("xhttp_endpoint_local", code, passed=xhttp_ok)

        error_details = ""
        if not xhttp_ok:
            all_passed = False
            error_details += "[bold red]XHTTP Endpoint Diagnostics:[/bold red]\n\n"
            
            # Get verbose curl output
            curl_diag = run_shell(f"curl -v -s -o /dev/null https://{config.cdn_domain}/api/v1/sync --resolve {config.cdn_domain}:443:127.0.0.1 --max-time 5 -k 2>&1", capture=True)
            error_details += f"[cyan]1. curl -v output:[/cyan]\n{curl_diag.stdout.strip()[:1000]}\n\n"
            
            # Get Xray journalctl
            xray_diag = run_shell("journalctl -u xray --no-pager -n 20", capture=True)
            error_details += f"[cyan]2. Xray Logs (last 20 lines):[/cyan]\n{xray_diag.stdout.strip()}\n\n"
            
            # Get Nginx error.log
            nginx_diag = run_shell("tail -n 15 /var/log/nginx/error.log 2>/dev/null", capture=True)
            error_details += f"[cyan]3. Nginx error.log (last 15 lines):[/cyan]\n{nginx_diag.stdout.strip()}\n"

        if all_passed:
            return StepResult(success=True, message="All verification checks passed")
        else:
            return StepResult(success=False, error=error_details, message="Some checks failed — see output above")

    def verify(self, config, state) -> VerifyResult:
        return VerifyResult(passed=True, checks={"note": "run step for full verification"})
