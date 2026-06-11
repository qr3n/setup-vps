# setup_vps/steps/s05_certificates.py
import re
from datetime import datetime
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info


def _cert_path(domain: str) -> Path:
    return Path(f"/etc/letsencrypt/live/{domain}/fullchain.pem")


def _cert_expiry_days(domain: str) -> int:
    path = _cert_path(domain)
    if not path.exists():
        return -1
    result = run_shell(
        f"openssl x509 -enddate -noout -in {path}",
        capture=True,
    )
    match = re.search(r"notAfter=(.*)", result.stdout)
    if not match:
        return -1
    try:
        # openssl output example: notAfter=Jun 11 12:00:00 2026 GMT
        expiry_str = match.group(1).strip()
        expiry = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
        return (expiry - datetime.now()).days
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
            # Use ECDSA P-256 as per guide
            r = run_shell(
                f"certbot certonly --standalone --non-interactive --agree-tos "
                f"--email {config.email} -d {domain} "
                f"--key-type ecdsa --elliptic-curve secp256r1",
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
