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
