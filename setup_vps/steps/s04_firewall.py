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
        run_shell("ufw allow in on lo", log_path=log)

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
