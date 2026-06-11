# setup_vps/steps/s08_verify.py
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.steps.s01_system import SystemPreparationStep
from setup_vps.steps.s01b_hardware import HardwareTuningStep
from setup_vps.steps.s02_sysctl import SysctlStep
from setup_vps.steps.s03_ssh import SSHHardeningStep
from setup_vps.steps.s04_firewall import FirewallStep
from setup_vps.steps.s05_certificates import CertificatesStep
from setup_vps.steps.s06_nginx import NginxStep
from setup_vps.steps.s07_xray import XrayStep
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_check_result, ask_confirm, print_error, print_warning


ALL_STEPS = [
    SystemPreparationStep(),
    HardwareTuningStep(),
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

        # 1. XHTTP check
        print_info("Checking XHTTP endpoint...")
        xhttp = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"https://{config.cdn_domain}/api/v1/sync/ "
            f"--resolve {config.cdn_domain}:443:127.0.0.1 "
            f"--max-time 5 -k",
            capture=True,
        )
        code = xhttp.stdout.strip()
        xhttp_ok = code in ("200", "400", "405")
        print_check_result("xhttp_endpoint_local", code, passed=xhttp_ok)
        
        if not xhttp_ok:
            print_error("XHTTP check failed.")
            if not ask_confirm("Continue anyway? (skip XHTTP failure)"):
                return StepResult(success=False, message="XHTTP verification failed and user stopped")
            print_warning("Skipping XHTTP failure.")
            all_passed = False

        # 2. TCP (Reality) check
        print_info("Checking TCP (Reality) port...")
        # Reality is on 1443 locally. We just check if it's open and responds to TLS
        tcp_check = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"https://{config.xray_reality_server_name}:1443 "
            f"--resolve {config.xray_reality_server_name}:1443:127.0.0.1 "
            f"--max-time 5 -k",
            capture=True,
        )
        tcp_code = tcp_check.stdout.strip()
        # Should return something (usually 200 or 301/302 from the target site)
        tcp_ok = tcp_check.returncode == 0 and tcp_code != ""
        print_check_result("tcp_reality_local", tcp_code or "no response", passed=tcp_ok)

        if not tcp_ok:
            print_error("TCP (Reality) check failed.")
            if not ask_confirm("Continue anyway? (skip TCP failure)"):
                return StepResult(success=False, message="TCP Reality verification failed and user stopped")
            print_warning("Skipping TCP failure.")
            all_passed = False

        # 3. Hysteria2 check
        print_info("Checking Hysteria2 (UDP) port...")
        # Hysteria2 is on 443 UDP. Locally we can check if it's listening
        udp_check = run_shell("ss -ulpn | grep :443 | grep xray", capture=True)
        hysteria_ok = udp_check.returncode == 0 and ":443" in udp_check.stdout
        print_check_result("hysteria2_udp_local", "listening" if hysteria_ok else "not found", passed=hysteria_ok)

        if not hysteria_ok:
            print_error("Hysteria2 check failed.")
            if not ask_confirm("Continue anyway? (skip Hysteria2 failure)"):
                return StepResult(success=False, message="Hysteria2 verification failed and user stopped")
            print_warning("Skipping Hysteria2 failure.")
            all_passed = False

        if all_passed:
            return StepResult(success=True, message="All verification checks passed")
        else:
            return StepResult(success=True, message="Verification finished with some skips")

    def verify(self, config, state) -> VerifyResult:
        return VerifyResult(passed=True, checks={"note": "run step for full verification"})
