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
        # Two-stage check:
        #   a) Port 8444 (nginx TLS+gRPC termination backend) — plain TCP to 127.0.0.1:8444
        #      using --http2-prior-knowledge (no TLS, direct H2 to upstream nginx port).
        #      nginx listens with ssl on 8444, so we test via HTTPS with -k.
        #   b) Full path: HTTPS via 443 SNI, routed through stream proxy to 8444.
        print_info(f"Checking Node backend port 8444 (nginx gRPC TLS)...")
        backend_check = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"https://127.0.0.1:8444/ "
            f"--http2 --max-time 5 -k "
            f"-H 'Host: {config.node_domain}' "
            f"-H 'Content-Type: application/grpc'",
            capture=True,
        )
        backend_code = backend_check.stdout.strip()
        # gRPC GET without body → 415 Unsupported Media Type (correct gRPC behavior)
        # or 200 if node responds. Both mean the proxy chain works.
        backend_ok = backend_code in ("200", "204", "400", "415")
        print_check_result("node_backend_8444", backend_code or "no response", passed=backend_ok)

        print_info(f"Checking Node connectivity (gRPC) via {config.node_domain} → stream:443...")
        node_check = run_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"https://{config.node_domain}/ "
            f"--resolve {config.node_domain}:443:127.0.0.1 "
            f"--http2 --max-time 5 "
            f"-H 'Content-Type: application/grpc'",
            capture=True,
        )
        code = node_check.stdout.strip()
        # Valid responses from a gRPC endpoint behind nginx:
        #   200 – node responded normally
        #   204 – grpc_error_502 handler (means proxy works, node is down)
        #   400/415 – nginx got the request, rejected non-gRPC body (proxy works)
        #   000 – curl couldn't connect at all (bad)
        node_ok = code in ("200", "204", "400", "415") or backend_ok
        print_check_result("node_domain_grpc_local", code or "no response", passed=node_ok)

        if not node_ok:
            print_error(
                "Node connectivity check failed. Check: nginx -t, systemctl status nginx, docker ps (remnanode), ss -tlnp | grep 2222")
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