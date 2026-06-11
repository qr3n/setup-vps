# setup_vps/config.py
import hashlib
import random
import secrets
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Config:
    main_domain: str = ""
    cdn_domain: str = ""
    server_ip: str = ""
    email: str = ""

    # SSH / knock
    ssh_knock_ports: list[int] = field(default_factory=list)

    # Xray
    xray_uuid: str = ""
    xray_reality_private_key: str = ""
    xray_reality_public_key: str = ""
    xray_reality_short_id: str = ""
    xray_reality_target: str = "www.microsoft.com:443"
    xray_reality_server_name: str = "www.microsoft.com"

    # Hysteria2
    hysteria2_auth_password: str = ""
    hysteria2_salamander_password: str = ""


def load_config(path: Path) -> Optional[Config]:
    path = Path(path)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def save_config(cfg: Config, path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(asdict(cfg), default_flow_style=False, allow_unicode=True))


def config_hash(cfg: Config) -> str:
    data = yaml.dump(asdict(cfg), default_flow_style=False, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def generate_secrets(cfg: Config):
    """Fill empty auto-generated fields in place. Does NOT overwrite existing values."""
    if not cfg.xray_uuid:
        cfg.xray_uuid = str(uuid.uuid4())
    if not cfg.ssh_knock_ports:
        ports = random.sample(range(5000, 65000), 3)
        cfg.ssh_knock_ports = sorted(ports)
    if not cfg.hysteria2_auth_password:
        cfg.hysteria2_auth_password = secrets.token_urlsafe(24)
    if not cfg.hysteria2_salamander_password:
        cfg.hysteria2_salamander_password = secrets.token_urlsafe(24)
    # xray keys generated after xray binary installed — done in s07
