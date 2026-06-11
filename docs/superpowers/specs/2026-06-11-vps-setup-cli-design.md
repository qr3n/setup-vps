# VPS Setup CLI — Design Spec

**Date:** 2026-06-11  
**Stack:** Python 3.11+, Rich, prompt_toolkit, Click, PyYAML  
**Target:** VLESS TCP REALITY + XHTTP + Hysteria2 на Ubuntu 22.04/Debian 12

---

## Context

Автоматизация гайда `xray-server-guide-final.md` — ручная настройка VPN сервера занимает 2-3 часа и легко сломать. Нужен CLI/TUI инструмент, который запускается **локально на сервере** (не remote SSH), выполняет каждый этап изолированно, сохраняет состояние при ошибках/остановках и верифицирует каждый шаг.

---

## Architecture

### Структура проекта

```
setup-vps/
├── setup_vps/
│   ├── __init__.py
│   ├── main.py              # entry point, главное меню
│   ├── config.py            # config.yaml load/save/validate/wizard
│   ├── state.py             # checkpoint: done/failed/pending/stale per step
│   ├── runner.py            # shell command executor, streaming output, logging
│   ├── ui.py                # Rich console helpers, prompt_toolkit menus
│   └── steps/
│       ├── __init__.py
│       ├── base.py          # BaseStep: preflight/run/verify/rollback interface
│       ├── s01_system.py    # Обновление, swap (2GB), hostname, ulimits
│       ├── s02_sysctl.py    # BBR, TCP buffers, SYN protection, sysctl apply
│       ├── s03_ssh.py       # SSH hardening + knockd install/config
│       ├── s04_firewall.py  # UFW reset + rules (80, 443/tcp, 443/udp, 20000-50000/udp)
│       ├── s05_certificates.py  # certbot + 2 домена (main + cdn)
│       ├── s06_nginx.py     # nginx install, stream config, SNI routing, HTTP vhosts
│       ├── s07_xray.py      # xray install, keygen, config (VLESS+REALITY, XHTTP, Hysteria2)
│       └── s08_verify.py    # финальные проверки всего стека
├── config.yaml              # генерируется wizard-ом, редактируется вручную
├── state.json               # checkpoint state (gitignore)
├── logs/                    # per-step логи с timestamp
│   ├── s01_system.log
│   └── ...
├── pyproject.toml
└── README.md
```

### BaseStep interface

Каждый шаг наследует `BaseStep`:

```python
class BaseStep:
    name: str        # "s01_system"
    title: str       # "System Preparation"
    
    def preflight(self, config, state) -> bool:
        """Idempotent check: уже выполнено? Пропустить run()."""
    
    def run(self, config, state) -> StepResult:
        """Выполнение. Каждая sub-task логируется отдельно."""
    
    def verify(self, config, state) -> VerifyResult:
        """Реальные проверки что изменения применились."""
    
    def rollback(self, config, state) -> None:
        """Откат где возможно (best-effort)."""
```

---

## UI/UX

### Главное меню (prompt_toolkit, keyboard-only)

```
╔══════════════════════════════════════════╗
║  VPS Setup — main.example.com            ║
║  Status: 4/8 steps complete              ║
╠══════════════════════════════════════════╣
║  [1] System Preparation       ✓ done     ║
║  [2] Kernel / Sysctl          ✓ done     ║
║  [3] SSH + Port Knocking      ✓ done     ║
║  [4] Firewall (UFW)           ✓ done     ║
║  [5] SSL Certificates         ● pending  ║
║  [6] Nginx                    ● pending  ║
║  [7] Xray (manual finish)     ● pending  ║
║  [8] Final Verification       ● pending  ║
╠══════════════════════════════════════════╣
║  [r] Run all pending   [c] Config        ║
║  [v] Verify step       [q] Quit          ║
╚══════════════════════════════════════════╝
```

Статусы: `✓ done` | `✗ failed` | `● pending` | `⚠ stale` | `⟳ running`

### Подменю шага

```
[r] Run    [v] Verify only    [s] Show logs    [b] Back
```

### Выполнение (Rich Live)

- Spinner на каждую sub-task пока выполняется
- `✓` зелёный / `✗` красный после завершения
- Детальный лог команд в scrollable панели ниже
- При ошибке — full error output + опция `[r] retry / [s] skip / [q] quit`

---

## State Management

### state.json

```json
{
  "config_hash": "sha256:abc123...",
  "steps": {
    "s01_system":       {"status": "done",    "completed_at": "2026-06-11T14:00:00Z"},
    "s02_sysctl":       {"status": "done",    "completed_at": "2026-06-11T14:05:00Z"},
    "s03_ssh":          {"status": "failed",  "error": "knockd install failed", "attempted_at": "..."},
    "s04_firewall":     {"status": "pending"},
    "s05_certificates": {"status": "pending"},
    "s06_nginx":        {"status": "pending"},
    "s07_xray":         {"status": "pending"},
    "s08_verify":       {"status": "pending"}
  }
}
```

**Config hash logic:** при изменении `config.yaml` → шаги с зависимостью от изменённых полей помечаются `stale`, предлагается перезапустить.

**Idempotency:** `preflight()` каждого шага проверяет реальное состояние ОС, не только state.json. Можно запустить на частично настроенном сервере.

---

## Config

### config.yaml (генерируется wizard-ом)

```yaml
server:
  main_domain: main.example.com
  cdn_domain: cdn.example.com
  server_ip: 1.2.3.4
  email: admin@example.com   # для certbot

ssh:
  knock_ports: [7000, 8000, 9000]  # генерируется случайно если пусто

xray:
  uuid: ""                    # генерируется автоматически (uuid4)
  reality_private_key: ""     # генерируется xray x25519
  reality_public_key: ""      # пара к private
  reality_short_id: ""        # генерируется
  reality_target: "www.microsoft.com:443"
  reality_server_name: "www.microsoft.com"

hysteria2:
  auth_password: ""           # генерируется (openssl rand -base64 24)
  salamander_password: ""     # генерируется (openssl rand -base64 24)
```

Все пустые поля генерируются автоматически при первом запуске wizard-а.

---

## Steps Detail

### s01_system — System Preparation
**Sub-tasks:**
1. `apt update && apt full-upgrade -y`
2. `apt install -y` необходимые пакеты (curl, wget, gnupg, unattended-upgrades, etc.)
3. Swap 2GB: создать если нет (`/swapfile`)
4. ulimits: `/etc/security/limits.conf` + `/etc/systemd/system.conf`
5. hostname: установить если не совпадает с `server.main_domain`

**Verify:** `free -h` → swap > 0; `ulimit -n` (через login shell) → >=1000000; `hostname` совпадает

### s02_sysctl — Kernel Optimization
**Sub-tasks:**
1. Проверить kernel >= 5.4 (для BBR)
2. `modprobe tcp_bbr` если нужно
3. Записать `/etc/sysctl.d/99-server.conf` (BBR, TCP buffers 16MB, SYN protection, file descriptors, swappiness=1)
4. `sysctl --system`

**Verify:** `sysctl net.ipv4.tcp_congestion_control` == `bbr`; `sysctl net.core.default_qdisc` == `fq`

### s03_ssh — SSH Hardening + Port Knocking
**Sub-tasks:**
1. Генерация knock sequence (3 случайных порта 5000-65000) → сохранить в `config.yaml` и `/root/.knock_sequence`
2. `apt install -y knockd`
3. `/etc/knockd.conf` — sequence + openSSH/closeSSH commands
4. `/etc/sshd_config.d/hardening.conf` — PermitRootLogin (key only), PasswordAuthentication no, etc.
5. `systemctl enable --now knockd`
6. **ПРЕДУПРЕЖДЕНИЕ:** показать knock sequence перед применением, требовать подтверждения

**Verify:** `sshd -T | grep passwordauthentication` == `no`; `systemctl is-active knockd` == `active`

### s04_firewall — UFW
**Sub-tasks:**
1. `ufw --force reset`
2. `ufw default deny incoming && ufw default allow outgoing`
3. Открыть: 80/tcp, 443/tcp, 443/udp, 20000:50000/udp
4. SSH не открываем (knockd добавляет динамически)
5. `ufw --force enable`

**Verify:** `ufw status` парсинг → все нужные правила присутствуют

### s05_certificates — SSL Certificates
**Sub-tasks:**
1. `apt install -y certbot`
2. Остановить nginx если запущен (временно)
3. certbot standalone для `main_domain`
4. certbot standalone для `cdn_domain`
5. Проверить auto-renewal timer

**Verify:** `certbot certificates` → оба домена, expiry > 30 дней; TLS handshake `openssl s_client`

### s06_nginx — Nginx
**Sub-tasks:**
1. `apt install -y nginx`
2. Stream config: `/etc/nginx/modules-enabled/` + `nginx.conf` stream block (ssl_preread, SNI routing)
3. HTTP vhosts: redirect 80→443, XHTTP proxy для cdn домена, статика для main
4. `nginx -t && systemctl restart nginx`

**Verify:** `nginx -t` OK; `curl -I http://main_domain` → 301; `curl -sk https://main_domain` → ответ

### s07_xray — Xray Installation
**Sub-tasks:**
1. Установка xray-core (официальный install script)
2. Генерация UUID, x25519 keypair, short_id → сохранить в config.yaml
3. Записать `/usr/local/etc/xray/config.json` (3 inbound: VLESS+REALITY, XHTTP, Hysteria2)
4. `systemctl enable --now xray`
5. **Manual finish notice:** показать итоговые client config строки для ручного добавления в Remnawave

**Verify:** `systemctl is-active xray` == `active`; `xray run -test -config /usr/local/etc/xray/config.json` OK

### s08_verify — Final Verification
**Sub-tasks:** повторить verify всех предыдущих шагов + финальный `srv-check` скрипт из гайда

---

## Dependencies

```toml
[project]
name = "setup-vps"
requires-python = ">=3.11"
dependencies = [
    "rich>=13.0",
    "prompt-toolkit>=3.0",
    "click>=8.1",
    "pyyaml>=6.0",
]

[project.scripts]
setup-vps = "setup_vps.main:cli"
```

---

## Verification Strategy (end-to-end)

1. Запустить `setup-vps` без config → wizard запрашивает данные → `config.yaml` создан
2. Запустить `setup-vps` снова → главное меню, все шаги pending
3. `[r] Run all` → шаги 1-7 выполняются последовательно
4. Остановить принудительно посередине → перезапустить → completed шаги пропущены
5. `[8] Final Verification` → все проверки зелёные
6. Изменить `config.yaml` → stale шаги помечены → предложить перезапуск
