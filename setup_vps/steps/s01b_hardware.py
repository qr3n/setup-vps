from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

NIC_OFFLOADS_SERVICE = "/etc/systemd/system/nic-offloads.service"

NIC_SERVICE_CONTENT = """\
[Unit]
Description=NIC offload tuning
After=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '\\
  IFACE=$(ip route get 1.1.1.1 | grep -oP "dev \\\\K\\\\S+"); \\
  ethtool -K $IFACE gso on gro on tso on tx-checksumming on 2>/dev/null; \\
  ethtool -K $IFACE tx-udp-segmentation on 2>/dev/null || true; \\
  ethtool -K $IFACE rx-udp-gro-forwarding on 2>/dev/null || true; \\
  MAX_RX=$(ethtool -g $IFACE 2>/dev/null | awk "/Pre-set max/{{found=1}} found && /RX:/{{print \\$2; exit}}"); \\
  MAX_TX=$(ethtool -g $IFACE 2>/dev/null | awk "/Pre-set max/{{found=1}} found && /TX:/{{print \\$2; exit}}"); \\
  RX=$(( MAX_RX > 4096 ? 4096 : MAX_RX )); \\
  TX=$(( MAX_TX > 4096 ? 4096 : MAX_TX )); \\
  ethtool -G $IFACE rx $RX tx $TX 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
"""

class HardwareTuningStep(BaseStep):
    name = "s01b_hardware"
    title = "Hardware Tuning"
    description = "NIC ring buffers, UDP offloads, CPU governor"

    def preflight(self, config, state) -> bool:
        return Path(NIC_OFFLOADS_SERVICE).exists()

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        print_info("Creating NIC offloads service...")
        Path(NIC_OFFLOADS_SERVICE).write_text(NIC_SERVICE_CONTENT)
        
        run_shell("systemctl daemon-reload", log_path=log)
        run_shell("systemctl enable nic-offloads", log_path=log)
        run_shell("systemctl start nic-offloads", log_path=log)

        print_info("Setting CPU governor to performance...")
        # Try cpupower if available
        r = run_shell("cpupower frequency-set -g performance", log_path=log)
        if r.returncode != 0:
            print_info("CPU governor tuning not supported on this VPS (skipping)")

        return StepResult(success=True, message="Hardware tuning applied and persisted")

    def verify(self, config, state) -> VerifyResult:
        checks = {}
        
        service_active = run_shell("systemctl is-active nic-offloads", capture=True).stdout.strip()
        checks["nic_offloads_service"] = service_active
        
        iface = run_shell("ip route get 1.1.1.1 | grep -oP 'dev \\K\\S+'", capture=True).stdout.strip()
        if iface:
            offloads = run_shell(f"ethtool -k {iface}", capture=True).stdout
            checks["tx-udp-segmentation"] = "on" if "tx-udp-segmentation: on" in offloads else "off/unsupported"
            
            ring = run_shell(f"ethtool -g {iface}", capture=True).stdout
            checks["ring_buffer"] = "tuned" if "RX: 4096" in ring or "TX: 4096" in ring else "default/small"

        gov = run_shell("cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null", capture=True).stdout.strip()
        if gov:
            checks["cpu_governor"] = gov
        
        return VerifyResult(passed=(service_active == "active"), checks=checks)
