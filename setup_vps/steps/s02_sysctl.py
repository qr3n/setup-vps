# setup_vps/steps/s02_sysctl.py
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

SYSCTL_FILE = "/etc/sysctl.d/99-server.conf"
SYSCTL_MARKER = "# setup-vps: kernel optimization"

SYSCTL_CONF = """\
# setup-vps: kernel optimization (2026 update)

# ── Congestion Control ──────────────────────────────────────────────────────
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# ── TCP Buffers (16 MB max — safe for 2 GB RAM) ────────────────────────────
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 524288 16777216
net.ipv4.tcp_mem = 786432 1048576 1572864

# ── Networking: Queues & Backlog ───────────────────────────────────────────
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 8192
net.ipv4.tcp_max_syn_backlog = 8192

# ── BBR / Fast Open / MTU probing ──────────────────────────────────────────
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mtu_probing = 1

# ── TIME_WAIT / Port Range ──────────────────────────────────────────────────
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_max_tw_buckets = 1440000
net.ipv4.ip_local_port_range = 1024 65535

# ── File descriptors ────────────────────────────────────────────────────────
fs.file-max = 2000000
fs.nr_open = 2000000

# ── Swap: only on OOM threat ────────────────────────────────────────────────
vm.swappiness = 10
vm.vfs_cache_pressure = 50

# ── Security & Hardening ────────────────────────────────────────────────────
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.tcp_syncookies = 1

# ── RPS / UDP Offload support ───────────────────────────────────────────────
net.core.rps_sock_flow_entries = 32768
"""


class SysctlStep(BaseStep):
    name = "s02_sysctl"
    title = "Kernel / Sysctl"
    description = "BBRv3, TCP/UDP buffer tuning, 2026 performance baseline"

    def preflight(self, config, state) -> bool:
        if not Path(SYSCTL_FILE).exists():
            return False
        content = Path(SYSCTL_FILE).read_text()
        if SYSCTL_MARKER not in content:
            return False
        # Check one key new value
        fastopen = run_shell("sysctl -n net.ipv4.tcp_fastopen", capture=True)
        return fastopen.stdout.strip() == "3"

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

        return StepResult(success=True, message="Kernel optimization (2026) applied")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        def get_sysctl(key):
            val = run_shell(f"sysctl -n {key}", capture=True).stdout.strip()
            checks[key] = val
            return val

        bbr = get_sysctl("net.ipv4.tcp_congestion_control")
        fastopen = get_sysctl("net.ipv4.tcp_fastopen")
        port_range = get_sysctl("net.ipv4.ip_local_port_range")
        rmem_default = get_sysctl("net.core.rmem_default")

        passed = (
            bbr == "bbr" and 
            fastopen == "3" and 
            port_range == "1024\t65535" and
            rmem_default == "1048576"
        )
        return VerifyResult(passed=passed, checks=checks)

