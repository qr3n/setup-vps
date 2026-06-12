# setup_vps/steps/s09_configs.py
import json
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_box, console
from rich.syntax import Syntax

XRAY_BIN = "/usr/local/bin/xray"


class ClientConfigsStep(BaseStep):
    name = "s09_configs"
    title = "Client Configurations"
    description = "Generate and verify client JSON configs (TCP, XHTTP, Hysteria2)"

    def preflight(self, config, state) -> bool:
        return False

    def run(self, config, state) -> StepResult:
        configs_dir = Path("/root/client_configs")
        configs_dir.mkdir(parents=True, exist_ok=True)

        tcp_config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "port": 10808,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                    "tag": "socks-in"
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": config.main_domain,
                                "port": 443,
                                "users": [
                                    {
                                        "id": config.xray_uuid,
                                        "encryption": "none",
                                        "flow": "xtls-rprx-vision"
                                    }
                                ]
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "show": False,
                            "fingerprint": "chrome",
                            "serverName": config.xray_reality_server_name,
                            "publicKey": config.xray_reality_public_key,
                            "shortId": config.xray_reality_short_id,
                            "spiderX": "/"
                        }
                    },
                    "tag": "proxy"
                },
                {"protocol": "freedom", "tag": "direct"}
            ]
        }

        xhttp_config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "port": 10808,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                    "tag": "socks-in"
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": config.cdn_domain,
                                "port": 443,
                                "users": [{"id": config.xray_uuid, "encryption": "none"}]
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "xhttp",
                        "security": "tls",
                        "tlsSettings": {"serverName": config.cdn_domain},
                        "xhttpSettings": {
                            "path": "/api/v1/sync",
                            "host": config.cdn_domain,
                            "mode": "packet-up",
                            "extra": {
                                "xPaddingBytes": "150-1500",
                                "xPaddingHeader": "X-Request-ID",
                                "xPaddingKey": "rid",
                                "sessionKey": "X-Session",
                                "seqKey": "X-Seq",
                                "noKeepAlive": True,
                                "noGRPC": True
                            }
                        }
                    },
                    "tag": "proxy"
                },
                {"protocol": "freedom", "tag": "direct"}
            ]
        }

        hysteria_config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "port": 10808,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                    "tag": "socks-in"
                }
            ],
            "outbounds": [
                {
                    "protocol": "hysteria",
                    "settings": {
                        "servers": [
                            {
                                "address": config.main_domain,
                                "port": 443,
                                "password": config.hysteria2_auth_password,
                                "version": 2
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "hysteria",
                        "hysteriaSettings": {"version": 2}
                    },
                    "tag": "proxy"
                },
                {"protocol": "freedom", "tag": "direct"}
            ]
        }

        files = {
            "vless_tcp_reality.json": tcp_config,
            "vless_xhttp_cdn.json": xhttp_config,
            "hysteria2.json": hysteria_config,
        }

        for name, data in files.items():
            path = configs_dir / name
            path.write_text(json.dumps(data, indent=2))
            print_info(f"Saved {name} to {path}")

            # Verify with xray if possible
            if Path(XRAY_BIN).exists():
                r = run_shell(f"{XRAY_BIN} run -test -config {path}", capture=True)
                if r.returncode == 0:
                    print_info(f"  [green]✓[/green] {name} verification passed")
                else:
                    print_info(f"  [red]✗[/red] {name} verification failed")
                    console.print(r.stderr, style="dim")

        # Display them
        for name, data in files.items():
            json_str = json.dumps(data, indent=2)
            syntax = Syntax(json_str, "json", theme="monokai", line_numbers=True)
            print_box(f"Client Config: {name}", syntax)

        return StepResult(success=True, message=f"Client configurations generated in {configs_dir}")

    def verify(self, config, state) -> VerifyResult:
        configs_dir = Path("/root/client_configs")
        passed = configs_dir.exists() and any(configs_dir.iterdir())
        return VerifyResult(passed=passed, checks={"client_configs_exist": "yes" if passed else "no"})
