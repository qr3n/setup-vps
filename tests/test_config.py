# tests/test_config.py
import pytest
import yaml
from pathlib import Path
from setup_vps.config import Config, load_config, save_config, config_hash, generate_secrets


def test_load_config_missing_returns_none(tmp_path):
    assert load_config(tmp_path / "config.yaml") is None


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.main_domain == "main.example.com"
    assert loaded.cdn_domain == "cdn.example.com"


def test_config_hash_deterministic(tmp_path):
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    h1 = config_hash(cfg)
    h2 = config_hash(cfg)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_config_hash_changes_on_mutation(tmp_path):
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    h1 = config_hash(cfg)
    cfg.main_domain = "other.example.com"
    h2 = config_hash(cfg)
    assert h1 != h2


def test_generate_secrets_fills_empty_fields():
    cfg = Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        server_ip="1.2.3.4",
        email="admin@example.com",
    )
    assert cfg.xray_uuid == ""
    generate_secrets(cfg)
    assert cfg.xray_uuid != ""
    assert cfg.hysteria2_auth_password != ""
    assert cfg.hysteria2_salamander_password != ""
    assert len(cfg.ssh_knock_ports) == 3
    # Check if they are valid UUID and secrets
    import uuid
    uuid.UUID(cfg.xray_uuid)
