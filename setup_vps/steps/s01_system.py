# setup_vps/steps/s01_system.py
import re
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info


SWAP_FILE = "/swapfile"
SWAP_SIZE_MB = 2048
LIMITS_CONF = "/etc/security/limits.conf"
SYSTEMD_CONF = "/etc/systemd/system.conf"
SYSCONF_LIMITS_MARKER = "# setup-vps: ulimits"

REQUIRED_PACKAGES = [
    "curl", "wget", "gnupg", "ca-certificates",
    "unattended-upgrades", "apt-listchanges",
    "ufw", "knockd", "certbot",
    "net-tools", "lsof", "lsb-release"
]


class SystemPreparationStep(BaseStep):
    name = "s01_system"
    title = "System Preparation"
    description = "apt upgrade, swap 2GB, ulimits, hostname"

    def preflight(self, config, state) -> bool:
        # Check: swap exists AND ulimits set AND hostname correct
        swap = run_shell("swapon --show --bytes", capture=True)
        has_swap = Path(SWAP_FILE).exists() and swap.returncode == 0 and SWAP_FILE in swap.stdout
        limits = Path(LIMITS_CONF).read_text() if Path(LIMITS_CONF).exists() else ""
        has_limits = SYSCONF_LIMITS_MARKER in limits
        hostname = run_shell("hostname -f", capture=True).stdout.strip()
        has_hostname = hostname == config.main_domain if config else False
        return has_swap and has_limits and has_hostname

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        from setup_vps.ui import run_with_live_logs

        def apt_update(on_output):
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            return run_shell("apt-get update -qq && apt-get full-upgrade -yq", log_path=log, on_output=on_output, env=env)

        print_info("Updating apt packages...")
        r = run_with_live_logs("Apt Update & Upgrade", apt_update)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="apt upgrade failed")

        def apt_install(on_output):
            pkgs = " ".join(REQUIRED_PACKAGES)
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            return run_shell(f"apt-get install -yq {pkgs}", log_path=log, on_output=on_output, env=env)

        print_info(f"Installing required packages...")
        r = run_with_live_logs("Installing dependencies", apt_install)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="package install failed")

        print_info("Configuring swap (2GB)...")
        if not Path(SWAP_FILE).exists():
            cmds = [
                f"dd if=/dev/zero of={SWAP_FILE} bs=1M count={SWAP_SIZE_MB} status=none",
                f"chmod 600 {SWAP_FILE}",
                f"mkswap {SWAP_FILE}",
                f"swapon {SWAP_FILE}",
            ]
            for cmd in cmds:
                r = run_shell(cmd, log_path=log)
                if r.returncode != 0:
                    return StepResult(success=False, error=r.stderr, message=f"swap: '{cmd}' failed")
            # persist in fstab
            fstab = Path("/etc/fstab").read_text()
            if SWAP_FILE not in fstab:
                with open("/etc/fstab", "a") as f:
                    f.write(f"\n{SWAP_FILE} none swap sw 0 0\n")

        print_info("Configuring ulimits...")
        limits_content = Path(LIMITS_CONF).read_text() if Path(LIMITS_CONF).exists() else ""
        if SYSCONF_LIMITS_MARKER not in limits_content:
            with open(LIMITS_CONF, "a") as f:
                f.write(f"\n{SYSCONF_LIMITS_MARKER}\n")
                f.write("* soft nofile 1000000\n")
                f.write("* hard nofile 1000000\n")
                f.write("root soft nofile 1000000\n")
                f.write("root hard nofile 1000000\n")

        systemd_conf = Path(SYSTEMD_CONF)
        content = systemd_conf.read_text() if systemd_conf.exists() else ""
        if "DefaultLimitNOFILE" not in content:
            with open(SYSTEMD_CONF, "a") as f:
                f.write("\nDefaultLimitNOFILE=1000000\n")
            run_shell("systemctl daemon-reexec", log_path=log)

        print_info(f"Setting hostname to {config.main_domain}...")
        run_shell(f"hostnamectl set-hostname {config.main_domain}", log_path=log)

        return StepResult(success=True, message="System preparation complete")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        # swap check
        swap = run_shell("free -m", capture=True)
        match = re.search(r"Swap:\s+(\d+)", swap.stdout)
        swap_mb = int(match.group(1)) if match else 0
        checks["swap_mb"] = f"{swap_mb}MB"
        swap_ok = swap_mb >= 1024

        # ulimits check (login shell)
        ulimit = run_shell("bash -l -c 'ulimit -n'", capture=True)
        ulimit_val = ulimit.stdout.strip()
        try:
            ulimit_n = int(ulimit_val)
        except ValueError:
            ulimit_n = 0
        checks["ulimit_nofile"] = ulimit_val
        ulimit_ok = ulimit_n >= 100000  # login shell may not show 1M but /etc/limits will

        # hostname
        hostname = run_shell("hostname -f", capture=True).stdout.strip()
        checks["hostname"] = hostname
        hostname_ok = hostname == config.main_domain if config else False

        passed = swap_ok and ulimit_ok and hostname_ok
        return VerifyResult(passed=passed, checks=checks)
