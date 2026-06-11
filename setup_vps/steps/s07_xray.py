# setup_vps/steps/s07_xray.py
import json
import re
import secrets
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_box
from setup_vps.config import save_config

XRAY_CONF = "/usr/local/etc/xray/config.json"
XRAY_BIN = "/usr/local/bin/xray"


def _xray_config(cfg) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "vless-reality",
                "listen": "127.0.0.1",
                "port": 1443,
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": cfg.xray_uuid, "flow": "xtls-rprx-vision"}],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": cfg.xray_reality_target,
                        "xver": 0,
                        "serverNames": [cfg.xray_reality_server_name],
                        "privateKey": cfg.xray_reality_private_key,
                        "shortIds": [cfg.xray_reality_short_id],
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "tag": "vless-xhttp",
                "listen": "127.0.0.1",
                "port": 8001,
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": cfg.xray_uuid}],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "tls",
                    "tlsSettings": {
                        "alpn": ["h2", "http/1.1"],
                        "certificates": [{
                            "certificateFile": f"/etc/letsencrypt/live/{cfg.cdn_domain}/fullchain.pem",
                            "keyFile": f"/etc/letsencrypt/live/{cfg.cdn_domain}/privkey.pem"
                        }]
                    },
                    "xhttpSettings": {
                        "path": "/api/v1/sync",
                        "host": cfg.cdn_domain,
                        "mode": "packet-up",
                        "extra": {
                            "noKeepAlive": True,
                            "noGRPC": True
                        }
                    },
                },
            },
            {
                "tag": "hysteria2",
                "listen": "0.0.0.0",
                "port": 443,
                "protocol": "hysteria",
                "settings": {
                    "version": 2,
                    "ignoreClientBandwidth": True,
                    "users": [{"password": cfg.hysteria2_auth_password}],
                },
                "streamSettings": {
                    "network": "hysteria",
                    "security": "tls",
                    "tlsSettings": {
                        "certificates": [{
                            "certificateFile": f"/etc/letsencrypt/live/{cfg.main_domain}/fullchain.pem",
                            "keyFile": f"/etc/letsencrypt/live/{cfg.main_domain}/privkey.pem",
                        }],
                    },
                    "hysteriaSettings": {
                        "version": 2,
                    },
                    "finalmask": {
                        "quicParams": {
                            "congestion": "bbr",
                            "initStreamReceiveWindow": 6291456,
                            "maxStreamReceiveWindow": 6291456,
                            "initConnectionReceiveWindow": 15728640,
                            "maxConnectionReceiveWindow": 15728640,
                        }
                    }
                },
            },
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "blocked"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
                {"type": "field", "network": "tcp,udp", "outboundTag": "direct"}
            ]
        },
    }


class XrayStep(BaseStep):
    name = "s07_xray"
    title = "Xray"
    description = "xray-core install, key generation, VLESS+REALITY+XHTTP+Hysteria2 config"

    def preflight(self, config, state) -> bool:
        if not Path(XRAY_BIN).exists():
            return False
        if not Path(XRAY_CONF).exists():
            return False
        active = run_shell("systemctl is-active xray", capture=True).stdout.strip()
        return active == "active"

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        if not Path(XRAY_BIN).exists():
            print_info("Installing xray-core...")
            # Use the official install script
            r = run_shell(
                "bash -c \"$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install",
                log_path=log,
            )
            if r.returncode != 0 or not Path(XRAY_BIN).exists():
                return StepResult(success=False, error=r.stderr, message="xray install failed")

        # Generate keys if not set
        if not config.xray_reality_private_key:
            print_info("Generating x25519 keypair...")
            r = run_shell(f"{XRAY_BIN} x25519", capture=True)
            if r.returncode != 0:
                return StepResult(success=False, error=r.stderr, message="xray x25519 keygen failed")
            
            priv_match = re.search(r"PrivateKey:\s+(.+)", r.stdout)
            pub_match = re.search(r"Password \(PublicKey\):\s+(.+)", r.stdout)
            if priv_match and pub_match:
                config.xray_reality_private_key = priv_match.group(1).strip()
                config.xray_reality_public_key = pub_match.group(1).strip()

        if not config.xray_reality_short_id:
            config.xray_reality_short_id = secrets.token_hex(8)

        # Save updated config with generated keys
        save_config(config, Path("config.yaml"))

        print_info("Writing xray config.json...")
        Path(XRAY_CONF).parent.mkdir(parents=True, exist_ok=True)
        Path(XRAY_CONF).write_text(json.dumps(_xray_config(config), indent=2))
        
        print_info("Configuring Xray to run as root...")
        override_dir = Path("/etc/systemd/system/xray.service.d")
        override_dir.mkdir(parents=True, exist_ok=True)
        override_conf = override_dir / "override.conf"
        override_conf.write_text("[Service]\nUser=root\nGroup=root\n")
        run_shell("systemctl daemon-reload", log_path=log)

        print_info("Testing xray config...")
        r = run_shell(f"{XRAY_BIN} run -test -config {XRAY_CONF}", log_path=log)
        if r.returncode != 0:
            error_details = f"{r.stdout}\n{r.stderr}".strip()
            return StepResult(success=False, error=error_details, message="xray config test failed")

        print_info("Enabling and restarting xray service...")
        run_shell("systemctl enable xray", log_path=log)
        r = run_shell("systemctl restart xray", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="xray restart failed")

        # Set up port hopping redirect for Hysteria2
        print_info("Configuring iptables for Hysteria2 port hopping...")
        run_shell("iptables -t nat -A PREROUTING -p udp --dport 20000:50000 -j REDIRECT --to-ports 443", log_path=log)
        run_shell("ip6tables -t nat -A PREROUTING -p udp --dport 20000:50000 -j REDIRECT --to-ports 443", log_path=log)
        run_shell("iptables-save > /etc/iptables/rules.v4 2>/dev/null || true", log_path=log)

        # Show client config summary
        print_box(
            "[bold cyan]Xray Client Configuration[/bold cyan]",
            f"[bold]VLESS TCP REALITY:[/bold]\n"
            f"  Address:    [cyan]{config.main_domain}[/cyan]\n"
            f"  Port:       443\n"
            f"  UUID:       [cyan]{config.xray_uuid}[/cyan]\n"
            f"  Flow:       xtls-rprx-vision\n"
            f"  PublicKey:  [cyan]{config.xray_reality_public_key}[/cyan]\n"
            f"  ShortId:    [cyan]{config.xray_reality_short_id}[/cyan]\n"
            f"  SNI:        {config.xray_reality_server_name}\n\n"
            f"[bold]VLESS XHTTP (CDN):[/bold]\n"
            f"  Address:    [cyan]{config.cdn_domain}[/cyan]\n"
            f"  Port:       443, Path: /api/v1/sync\n"
            f"  UUID:       [cyan]{config.xray_uuid}[/cyan]\n\n"
            f"[bold]Hysteria2:[/bold]\n"
            f"  Address:    [cyan]{config.main_domain}[/cyan]:443\n"
            f"  Auth:       [cyan]{config.hysteria2_auth_password}[/cyan]\n\n"
            f"[dim]All keys also saved in config.yaml[/dim]",
            style="cyan",
        )

        return StepResult(success=True, message="Xray configured. See client config above.")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        test = run_shell(f"{XRAY_BIN} run -test -config {XRAY_CONF} 2>&1", capture=True)
        checks["xray_config_test"] = "ok" if test.returncode == 0 else test.stdout.strip()[:80]
        test_ok = test.returncode == 0

        active = run_shell("systemctl is-active xray", capture=True).stdout.strip()
        checks["xray_active"] = active
        active_ok = active == "active"

        passed = test_ok and active_ok
        return VerifyResult(passed=passed, checks=checks)
