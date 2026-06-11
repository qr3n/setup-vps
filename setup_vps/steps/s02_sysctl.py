# setup_vps/steps/s02_sysctl.py
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

SYSCTL_FILE = "/etc/sysctl.d/99-server.conf"
SYSCTL_MARKER = "# setup-vps: kernel optimization"

SYSCTL_CONF = """\
# setup-vps: kernel optimization

# ── Congestion Control ──────────────────────────────────────────────────────
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr

# ── TCP Buffers (16 MB max — safe for 2 GB RAM) ────────────────────────────
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_wmem=4096 65536 16777216

# ── TIME_WAIT tuning ────────────────────────────────────────────────────────
net.ipv4.tcp_max_tw_buckets=1440000
net.ipv4.tcp_tw_reuse=1
net.ipv4.ip_local_port_range=1024 65535

# ── Keepalive ───────────────────────────────────────────────────────────────
net.ipv4.tcp_keepalive_time=60
net.ipv4.tcp_keepalive_intvl=10
net.ipv4.tcp_keepalive_probes=6

# ── SYN flood protection ────────────────────────────────────────────────────
net.ipv4.tcp_syncookies=1
net.ipv4.tcp_syn_retries=2
net.ipv4.tcp_synack_retries=2

# ── File descriptors ────────────────────────────────────────────────────────
fs.file-max=2000000
fs.nr_open=2000000

# ── Swap: only on OOM threat ────────────────────────────────────────────────
vm.swappiness=1
vm.vfs_cache_pressure=50

# ── Spoof protection ────────────────────────────────────────────────────────
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
"""


class SysctlStep(BaseStep):
    name = "s02_sysctl"
    title = "Kernel / Sysctl"
    description = "BBR congestion control, TCP buffer tuning, security hardening"

    def preflight(self, config, state) -> bool:
        if not Path(SYSCTL_FILE).exists():
            return False
        content = Path(SYSCTL_FILE).read_text()
        if SYSCTL_MARKER not in content:
            return False
        bbr = run_shell("sysctl -n net.ipv4.tcp_congestion_control", capture=True)
        return bbr.stdout.strip() == "bbr"

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        print_info("Checking kernel version for BBR support...")
        uname = run_shell("uname -r", capture=True)
        kernel = uname.stdout.strip()
        print_info(f"Kernel: {kernel}")

        print_info("Loading tcp_bbr module...")
        run_shell("modprobe tcp_bbr", log_path=log)

        print_info(f"Writing {SYSCTL_FILE}...")
        Path(SYSCTL_FILE).write_text(SYSCTL_CONF)

        print_info("Applying sysctl settings...")
        r = run_shell("sysctl --system", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="sysctl --system failed")

        return StepResult(success=True, message="Kernel optimization applied")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        bbr = run_shell("sysctl -n net.ipv4.tcp_congestion_control", capture=True).stdout.strip()
        checks["tcp_congestion_control"] = bbr
        bbr_ok = bbr == "bbr"

        qdisc = run_shell("sysctl -n net.core.default_qdisc", capture=True).stdout.strip()
        checks["default_qdisc"] = qdisc
        qdisc_ok = qdisc == "fq"

        swappiness = run_shell("sysctl -n vm.swappiness", capture=True).stdout.strip()
        checks["vm.swappiness"] = swappiness
        swap_ok = swappiness == "1"

        passed = bbr_ok and qdisc_ok and swap_ok
        return VerifyResult(passed=passed, checks=checks)
