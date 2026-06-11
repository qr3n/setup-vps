# Итоговый гайд: VLESS TCP REALITY + XHTTP + Hysteria2 — 2026

> **Xray-core v26.6.1 | Remnawave | Ubuntu 22.04/Debian 12**  
> Синтез: setup-guide-v4.1, vpn-stealth-guide, исследований VLESS/XHTTP/H2, Hysteria2/Xray26 DPI-отчёта  
> Исправления к источникам задокументированы в разделе «Примечания»

---

## Итоговая архитектура

```
СНАРУЖИ ВИДНО:
  :80     → Nginx HTTP  (certbot + редирект)
  :443    → Nginx stream (ssl_preread, TCP passthrough)
  :443/UDP→ Xray Hysteria2 inbound

NGINX STREAM routing по SNI:
  SNI = main.your.domain → Xray :20001 (VLESS TCP REALITY + Vision)
  SNI = cdn.your.domain  → Xray :20002 (VLESS XHTTP TLS)
  SNI = чужой/нет        → Nginx :8443 (HTTPS фейк-сайт)

XRAY внутри (недоступно снаружи):
  :20001 → VLESS TCP REALITY selfsteal → Nginx :8443 (при зондировании)
  :20002 → VLESS XHTTP TLS            → fallback → Nginx :8080 (plain HTTP)
  :443/UDP→ Hysteria2 + masquerade

СНАРУЖИ ПРИ СКАНИРОВАНИИ:
  nmap 31.76.94.11  → открыто :80 (HTTP) и :443 (HTTPS)
  :22               → filtered (knockd, не open)
  :2095             → filtered (iptables)
  curl https://main.your.domain → реальный корпоративный сайт
  curl https://cdn.your.domain  → CDN-лендинг
  openssl active probe :443     → сертификат + HTML вашего сайта
```

---

## 0. DNS — предварительное требование

Нужно **три субдомена** (один домен, $10/год):

```
main.your.domain  → A-запись → IP_СЕРВЕРА   (VLESS TCP REALITY + Hysteria2)
cdn.your.domain   → A-запись → IP_СЕРВЕРА   (XHTTP; позже CNAME на CDN)
panel.your.domain → A-запись → IP_СЕРВЕРА   (remnawave API, опционально)

TTL: 300 (5 минут)
```

Проверка после пропагации (~5 мин):
```bash
dig +short main.your.domain   # → IP_СЕРВЕРА
dig +short cdn.your.domain    # → IP_СЕРВЕРА
```

---

## 1. Базовая настройка системы

### 1.1 Подключение и обновление

```bash
ssh root@IP_СЕРВЕРА

apt update && apt full-upgrade -y
apt install -y \
  curl wget git htop iotop net-tools lsof unzip jq \
  ufw knockd fail2ban \
  unattended-upgrades apt-listchanges \
  chrony iptables-persistent

timedatectl set-timezone UTC
hostnamectl set-hostname cdn-node
echo "127.0.0.1 cdn-node" >> /etc/hosts
```

### 1.2 Синхронизация времени — ОБЯЗАТЕЛЬНО для REALITY

```bash
# REALITY отклоняет соединение если время сервера расходится с клиентом > 60 сек
systemctl enable chrony --now
timedatectl set-ntp true

# Проверка:
timedatectl status
# → synchronized: yes  ← обязательно
```

### 1.3 Автоматические обновления безопасности

```bash
cat > /etc/apt/apt.conf.d/50unattended-upgrades << 'EOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::DevRelease "false";
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::MinimalSteps "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::SyslogEnable "true";
EOF

cat > /etc/apt/apt.conf.d/20auto-upgrades << 'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

systemctl enable unattended-upgrades && systemctl start unattended-upgrades
```

### 1.4 Swap — 2 GB

```bash
swapon --show   # убедиться что swap ещё не создан

fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

free -h   # Swap: 2.0G — ОК
```

### 1.5 Sysctl — оптимизация ядра

> **Примечание к источникам:** setup-guide-v4.1 использовал буферы 134 MB (`net.core.rmem_max=134217728`).  
> Для сервера с **2 GB RAM** это расточительно — при множестве соединений возможен OOM.  
> Ниже — **16 MB max**, безопасно для 2 GB RAM и достаточно для 10 Gbps/50ms RTT каналов.

```bash
# Проверить поддержку BBR (нужно ядро 5.4+)
uname -r
sysctl net.ipv4.tcp_available_congestion_control | grep bbr || modprobe tcp_bbr

cat > /etc/sysctl.d/99-server.conf << 'EOF'
# ── Congestion Control ────────────────────────────────────────────────────────
# BBR + fq: оптимальная пара для высокого RTT и нестабильных каналов
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr

# ── Буферы TCP (16 MB max для 2 GB RAM) ──────────────────────────────────────
# 128 MB из некоторых гайдов — опасно: при 500+ соединениях → OOM на 2GB сервере
# 16 MB достаточно для BDP 10Gbps/50ms (~62 MB), реально ограничено RAM
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.core.rmem_default=1048576
net.core.wmem_default=1048576
net.ipv4.tcp_rmem=4096 1048576 16777216
net.ipv4.tcp_wmem=4096 524288 16777216

# ── Буферы UDP (критично для Hysteria2 / QUIC) ───────────────────────────────
# quic-go официально рекомендует минимум 7 MB, 16 MB — оптимально
# Те же net.core.rmem_max/wmem_max применяются к UDP

# ── Очереди ───────────────────────────────────────────────────────────────────
net.core.somaxconn=65535
net.core.netdev_max_backlog=16384
net.ipv4.tcp_max_syn_backlog=65535

# ── TIME_WAIT (критично для XHTTP packet-up) ──────────────────────────────────
# В packet-up каждый POST = новое TCP-соединение → много TIME_WAIT сокетов
# tcp_tw_reuse: переиспользовать TIME_WAIT для исходящих → снижает накопление
# tcp_max_tw_buckets 524288 (не 2M! — 2M = ~488 MB kernel memory на 2GB)
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_max_tw_buckets=524288
net.ipv4.tcp_fin_timeout=10

# ── Keepalive ─────────────────────────────────────────────────────────────────
net.ipv4.tcp_keepalive_time=60
net.ipv4.tcp_keepalive_intvl=10
net.ipv4.tcp_keepalive_probes=6

# ── Эфемерные порты ───────────────────────────────────────────────────────────
# ВАЖНО: минимум 10000, не 1024 — диапазон 1024-9999 содержит системные порты
net.ipv4.ip_local_port_range=10000 65535

# ── MTU probing + TCP Fast Open ───────────────────────────────────────────────
net.ipv4.tcp_mtu_probing=1
net.ipv4.tcp_fastopen=3

# ── Защита от SYN-flood ───────────────────────────────────────────────────────
net.ipv4.tcp_syncookies=1
net.ipv4.tcp_syn_retries=2
net.ipv4.tcp_synack_retries=2

# ── Файловые дескрипторы ──────────────────────────────────────────────────────
fs.file-max=2000000
fs.nr_open=2000000

# ── Swap: только при угрозе OOM ───────────────────────────────────────────────
# Docker overlay2 + swap = резкая деградация производительности
vm.swappiness=1
vm.vfs_cache_pressure=50

# ── Защита от спуфинга ────────────────────────────────────────────────────────
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
EOF

sysctl --system

# Проверки:
sysctl net.ipv4.tcp_congestion_control
# → bbr
sysctl net.ipv4.ip_local_port_range
# → 10000    65535
```

### 1.6 Лимиты файловых дескрипторов

```bash
cat > /etc/security/limits.d/99-server.conf << 'EOF'
* soft nofile 1000000
* hard nofile 1000000
root soft nofile 1000000
root hard nofile 1000000
EOF

mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/limits.conf << 'EOF'
[Manager]
DefaultLimitNOFILE=1000000
DefaultLimitNPROC=65535
DefaultTasksMax=infinity
EOF

systemctl daemon-reload
```

---

## 2. SSH: невидимость через port knocking

### 2.1 SSH-ключ (выполнять на своём ПК)

```bash
# На своём компьютере:
ssh-keygen -t ed25519 -C "vpn-server-$(date +%Y%m)" -f ~/.ssh/vpn_server_key

# Копируем публичный ключ на сервер (пока SSH работает стандартно):
ssh-copy-id -i ~/.ssh/vpn_server_key.pub root@IP_СЕРВЕРА

# Проверяем вход по ключу (без пароля):
ssh -i ~/.ssh/vpn_server_key root@IP_СЕРВЕРА && echo "Ключ работает"
```

### 2.2 Настройка SSH-демона

```bash
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup

cat > /etc/ssh/sshd_config << 'EOF'
Port 22
PermitRootLogin prohibit-password
PasswordAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PermitEmptyPasswords no
ChallengeResponseAuthentication no
KerberosAuthentication no
GSSAPIAuthentication no
X11Forwarding no
AllowAgentForwarding no
PrintMotd no
UseDNS no
UsePAM yes

HostKey /etc/ssh/ssh_host_ed25519_key
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com
MACs hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com

MaxAuthTries 3
LoginGraceTime 20
ClientAliveInterval 120
ClientAliveCountMax 3
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

sshd -t && echo "SSH config OK"
systemctl restart sshd
echo "SSH работает, продолжаем"
```

### 2.3 Knockd — TCP SYN port knocking

> **Почему TCP, а не UDP:** UDP пакеты теряются на маршруте. TCP SYN надёжнее.  
> **Конфликт UFW + knockd:** knockd вставляет правила в iptables напрямую, а `ufw reload`  
> их стирает. Решение: отдельная цепочка KNOCKD в before.rules, которую UFW не трогает.

```bash
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')
echo "Интерфейс: $IFACE"

# Генерация последовательности (криптографически)
K1=$(shuf -i 30000-60000 -n 1)
K2=$(shuf -i 30000-60000 -n 1)
K3=$(shuf -i 30000-60000 -n 1)
echo "Knock sequence: $K1 $K2 $K3  ← СОХРАНИ ЭТО!"

# Шаг А: добавляем объявление цепочки KNOCKD в before.rules (атомарно с UFW)
awk '/^\*filter$/{print; print ":KNOCKD - [0:0]"; next} 1' \
    /etc/ufw/before.rules > /tmp/ufw_br.tmp \
  && mv /tmp/ufw_br.tmp /etc/ufw/before.rules

# Шаг Б: прыжок в KNOCKD первым правилом ufw-before-input
awk '/^-A ufw-before-input/{if(!done){print "-A ufw-before-input -j KNOCKD"; done=1}} 1' \
    /etc/ufw/before.rules > /tmp/ufw_br.tmp \
  && mv /tmp/ufw_br.tmp /etc/ufw/before.rules

# Применяем сейчас (без ребута):
iptables -N KNOCKD 2>/dev/null || true
iptables -C ufw-before-input -j KNOCKD 2>/dev/null \
  || iptables -I ufw-before-input 1 -j KNOCKD

cat > /etc/knockd.conf << EOF
[options]
    UseSyslog
    Interface = $IFACE

[openSSH]
    sequence    = $K1,$K2,$K3
    seq_timeout = 15
    protocol    = tcp
    tcpflags    = syn
    command     = /sbin/iptables -I KNOCKD -s %IP% -p tcp --dport 22 -j ACCEPT
    cmd_timeout = 30
    stop_command = /sbin/iptables -D KNOCKD -s %IP% -p tcp --dport 22 -j ACCEPT
EOF

echo "Knock sequence: $K1 $K2 $K3" > /root/.knock_sequence
chmod 600 /root/.knock_sequence

systemctl enable knockd
systemctl start knockd
systemctl status knockd --no-pager
```

> **⚠️ Запиши knock-последовательность прямо сейчас** — без неё SSH заблокируется навсегда.

### 2.4 Подключение через knock (на своём ПК)

```bash
# Установка утилиты:
# macOS: brew install knock
# Linux: apt install knockd

# Подключение (K1 K2 K3 — твои числа из /root/.knock_sequence):
knock IP_СЕРВЕРА K1 K2 K3 -d 150 && sleep 1 && ssh -i ~/.ssh/vpn_server_key root@IP_СЕРВЕРА

# Алиас для удобства:
alias vpn-ssh='knock IP_СЕРВЕРА K1 K2 K3 -d 150 && sleep 1 && ssh -i ~/.ssh/vpn_server_key root@IP_СЕРВЕРА'
```

---

## 3. Firewall (UFW)

```bash
ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# Открываем только видимое снаружи:
ufw allow 80/tcp   comment 'HTTP: certbot + redirect'
ufw allow 443/tcp  comment 'HTTPS: весь VPN-трафик'
ufw allow 443/udp  comment 'Hysteria2 QUIC'
# Диапазон для Hysteria2 port hopping:
ufw allow 20000:50000/udp comment 'Hysteria2 port hopping'
# SSH — НЕ открываем, knockd добавляет правило динамически

ufw --force enable
ufw status verbose
```

```
Ожидаемый вывод:
80/tcp    ALLOW IN  Anywhere
443/tcp   ALLOW IN  Anywhere
443/udp   ALLOW IN  Anywhere
20000:50000/udp  ALLOW IN  Anywhere
```

---

## 4. Fail2ban

```bash
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime  = 3600
findtime = 300
maxretry = 10
backend  = systemd
ignoreip = 127.0.0.1/8 ::1

[nginx-limit-req]
enabled  = true
port     = http,https
filter   = nginx-limit-req
logpath  = /var/log/nginx/main-site.log
           /var/log/nginx/cdn-site.log
           /var/log/nginx/xray-fallback.log
maxretry = 20
bantime  = 7200

[nginx-botsearch]
enabled  = true
port     = http,https
filter   = nginx-botsearch
logpath  = /var/log/nginx/main-site.log
           /var/log/nginx/cdn-site.log
maxretry = 5
bantime  = 86400
EOF

systemctl enable fail2ban
systemctl restart fail2ban
fail2ban-client status
```

---

## 5. Фейковые сайты

### 5.1 Selfsteal-сайт для REALITY (main.your.domain)

```bash
mkdir -p /var/www/main-site

cat > /var/www/main-site/index.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DataSync Solutions — Enterprise API Platform</title>
  <meta name="description" content="Enterprise data synchronization and API gateway solutions">
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0f1e;color:#e2e8f0;line-height:1.6}
    header{padding:1.2rem 2rem;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1e2a3a}
    .logo{font-size:1.3rem;font-weight:700;color:#38bdf8}
    nav a{color:#94a3b8;text-decoration:none;margin-left:2rem;font-size:.9rem}
    nav a:hover{color:#e2e8f0}
    .hero{text-align:center;padding:7rem 2rem 5rem}
    h1{font-size:2.8rem;font-weight:800;color:#f1f5f9;margin-bottom:1rem}
    .sub{color:#94a3b8;font-size:1.1rem;max-width:520px;margin:0 auto 2.5rem}
    .btn{display:inline-block;padding:.8rem 2rem;background:#0ea5e9;color:#fff;border-radius:.5rem;text-decoration:none;font-weight:600;margin:.4rem}
    .btn-ghost{background:transparent;border:1px solid #334155;color:#e2e8f0}
    .features{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1.5rem;max-width:960px;margin:4rem auto;padding:0 2rem}
    .card{background:#111827;border:1px solid #1e2a3a;border-radius:.75rem;padding:1.5rem}
    .card h3{color:#e2e8f0;margin-bottom:.5rem;font-size:.95rem}
    .card p{color:#64748b;font-size:.875rem}
    .icon{font-size:1.5rem;margin-bottom:.75rem}
    footer{text-align:center;padding:3rem 2rem;color:#475569;font-size:.85rem;border-top:1px solid #1e2a3a;margin-top:4rem}
    footer a{color:#38bdf8;text-decoration:none}
  </style>
</head>
<body>
<header>
  <div class="logo">◈ DataSync</div>
  <nav>
    <a href="/docs/">Docs</a>
    <a href="/about.html">About</a>
    <a href="#">API</a>
    <a href="#">Login</a>
  </nav>
</header>
<div class="hero">
  <h1>Enterprise API Synchronization</h1>
  <p class="sub">High-throughput data gateway with real-time sync, versioning, and 99.99% uptime SLA.</p>
  <a href="/docs/" class="btn">Get Started</a>
  <a href="/about.html" class="btn btn-ghost">Learn More</a>
</div>
<div class="features">
  <div class="card"><div class="icon">⚡</div><h3>Real-Time Sync</h3><p>Sub-millisecond data propagation across all connected endpoints with conflict resolution.</p></div>
  <div class="card"><div class="icon">🔒</div><h3>End-to-End Encryption</h3><p>TLS 1.3, mutual authentication, and at-rest AES-256 encryption for all data flows.</p></div>
  <div class="card"><div class="icon">📊</div><h3>Advanced Analytics</h3><p>Live dashboards, API usage metrics, latency heatmaps, and custom alerting.</p></div>
  <div class="card"><div class="icon">🔧</div><h3>REST & gRPC API</h3><p>OpenAPI 3.0 spec, SDKs for Go, Python, Node.js. Terraform provider available.</p></div>
</div>
<footer>
  &copy; <script>document.write(new Date().getFullYear())</script> DataSync Solutions.
  <a href="#">Privacy</a> · <a href="#">Terms</a> · <a href="/docs/">Docs</a>
</footer>
</body>
</html>
EOF

cat > /var/www/main-site/about.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>About — DataSync Solutions</title>
<style>body{font-family:-apple-system,sans-serif;background:#0a0f1e;color:#e2e8f0;max-width:800px;margin:4rem auto;padding:0 2rem}
h1{color:#38bdf8}p{color:#94a3b8;line-height:1.7}a{color:#38bdf8}</style></head>
<body>
<h1>About DataSync Solutions</h1>
<p>Founded in 2019, DataSync Solutions provides enterprise-grade API synchronization infrastructure to over 1,200 companies worldwide.</p>
<p>Our platform handles more than 50 billion API calls per month with 99.99% uptime.</p>
<p><a href="/">← Back to Home</a></p>
</body>
</html>
EOF

cat > /var/www/main-site/robots.txt << 'EOF'
User-agent: *
Allow: /
Sitemap: https://main.your.domain/sitemap.xml
EOF
```

### 5.2 CDN-сайт (cdn.your.domain)

```bash
mkdir -p /var/www/cdn-site/docs

cat > /var/www/cdn-site/index.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TeleMost CDN — Global Content Delivery</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:-apple-system,sans-serif;background:#0f1117;color:#e2e8f0}
    header{padding:1.2rem 2rem;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1e2535}
    .logo{font-size:1.3rem;font-weight:700;color:#60a5fa}
    nav a{color:#94a3b8;text-decoration:none;margin-left:2rem;font-size:.9rem}
    .hero{text-align:center;padding:6rem 2rem 4rem}
    h1{font-size:2.8rem;font-weight:800;background:linear-gradient(135deg,#60a5fa,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:1rem}
    .sub{color:#94a3b8;font-size:1.1rem;max-width:520px;margin:0 auto 2.5rem}
    .btn{display:inline-block;padding:.8rem 2rem;background:#3b82f6;color:#fff;border-radius:.5rem;text-decoration:none;font-weight:600;margin:.4rem}
    .stats{display:flex;justify-content:center;gap:3rem;padding:2rem;flex-wrap:wrap}
    .stat .num{font-size:2rem;font-weight:700;color:#60a5fa;text-align:center}
    .stat .label{color:#64748b;font-size:.85rem;text-align:center}
    .features{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1.5rem;max-width:960px;margin:4rem auto;padding:0 2rem}
    .card{background:#1e2535;border:1px solid #2d3748;border-radius:.75rem;padding:1.5rem}
    .card h3{color:#e2e8f0;margin-bottom:.5rem;font-size:.95rem}
    .card p{color:#64748b;font-size:.875rem}
    .icon{font-size:1.5rem;margin-bottom:.75rem}
    footer{text-align:center;padding:3rem 2rem;color:#475569;font-size:.85rem;border-top:1px solid #1e2535;margin-top:4rem}
    footer a{color:#60a5fa;text-decoration:none}
  </style>
</head>
<body>
<header>
  <div class="logo">⚡ TeleMost CDN</div>
  <nav><a href="/docs/">Docs</a><a href="#">Pricing</a><a href="#">Dashboard</a></nav>
</header>
<div class="hero">
  <h1>Deliver Content<br>at the Speed of Light</h1>
  <p class="sub">200+ global PoPs, 10 Tb/s capacity, instant cache purge, and 99.99% SLA.</p>
  <a href="#" class="btn">Get Started Free</a>
  <a href="/docs/" class="btn" style="background:transparent;border:1px solid #334155;color:#e2e8f0">View Docs</a>
</div>
<div class="stats">
  <div class="stat"><div class="num">200+</div><div class="label">Global PoPs</div></div>
  <div class="stat"><div class="num">10 Tb/s</div><div class="label">Capacity</div></div>
  <div class="stat"><div class="num">99.99%</div><div class="label">Uptime SLA</div></div>
</div>
<div class="features">
  <div class="card"><div class="icon">🌍</div><h3>Global Edge</h3><p>200+ PoPs across 80 countries for sub-10ms latency.</p></div>
  <div class="card"><div class="icon">🔒</div><h3>TLS 1.3</h3><p>Auto certificate provisioning, HTTP/2 and HTTP/3 support.</p></div>
  <div class="card"><div class="icon">📊</div><h3>Analytics</h3><p>Real-time traffic graphs, cache hit rates, threat analytics.</p></div>
</div>
<footer>&copy; <script>document.write(new Date().getFullYear())</script> TeleMost CDN. <a href="#">Privacy</a> · <a href="#">Terms</a></footer>
</body>
</html>
EOF

cat > /var/www/cdn-site/docs/index.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Docs — TeleMost CDN</title>
<style>body{font-family:-apple-system,sans-serif;background:#0f1117;color:#e2e8f0;max-width:800px;margin:4rem auto;padding:0 2rem}
h1{color:#60a5fa}h2{color:#94a3b8;margin-top:2rem}code{background:#1e2535;padding:.2rem .4rem;border-radius:3px;font-size:.9em}
pre{background:#1e2535;padding:1rem;border-radius:.5rem;overflow-x:auto}</style></head>
<body>
<h1>TeleMost CDN Documentation</h1>
<h2>Quick Start</h2>
<p>Point your domain CNAME to <code>cdn.telemost.space</code> to get started.</p>
<h2>Cache Purge API</h2>
<pre><code>curl -X POST https://api.telemost.space/v1/purge \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"urls":["https://example.com/asset.js"]}'</code></pre>
<p><a href="/" style="color:#60a5fa">← Back to Home</a></p>
</body>
</html>
EOF

cat > /var/www/cdn-site/robots.txt << 'EOF'
User-agent: *
Disallow: /api/
Sitemap: https://cdn.your.domain/sitemap.xml
EOF

# Замените cdn.your.domain на реальный домен:
sed -i 's/cdn.your.domain/cdn.YOURDOMAIN.COM/g' /var/www/cdn-site/robots.txt

# Права:
chown -R www-data:www-data /var/www/main-site /var/www/cdn-site
find /var/www/main-site /var/www/cdn-site -type f -exec chmod 644 {} \;
find /var/www/main-site /var/www/cdn-site -type d -exec chmod 755 {} \;

echo "Сайты созданы"
```

---

## 6. SSL/TLS сертификаты (ECDSA P-256)

> **Почему P-256, а не RSA 4096 или P-384:**  
> — TLS handshake в 5–10 раз быстрее (критично для XHTTP packet-up: каждый POST — новый handshake)  
> — 99% реальных CDN и веб-серверов используют P-256 → маскировка лучше  
> — P-384 — аномалия, возможный fingerprint

### 6.1 Временный Nginx для certbot

```bash
apt install -y nginx certbot python3-certbot-nginx

rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/certbot

cat > /etc/nginx/sites-available/temp-acme << 'EOF'
server {
    listen 80;
    server_name main.your.domain cdn.your.domain;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 "ok"; }
}
EOF

ln -s /etc/nginx/sites-available/temp-acme /etc/nginx/sites-enabled/temp-acme
nginx -t && systemctl reload nginx
```

### 6.2 Получение сертификатов

```bash
# Замените main.your.domain и cdn.your.domain на реальные домены!
certbot certonly \
  --webroot -w /var/www/certbot \
  -d main.your.domain \
  --key-type ecdsa --elliptic-curve secp256r1 \
  --agree-tos -m admin@your.domain --non-interactive

certbot certonly \
  --webroot -w /var/www/certbot \
  -d cdn.your.domain \
  --key-type ecdsa --elliptic-curve secp256r1 \
  --agree-tos -m admin@your.domain --non-interactive

# Проверяем тип ключа:
openssl x509 -in /etc/letsencrypt/live/main.your.domain/fullchain.pem -noout -text \
  | grep -E 'Algorithm|Public Key'
# → Public Key Algorithm: id-ecPublicKey
# → ASN1 OID: prime256v1  ← P-256 подтверждён

# Тест автообновления:
certbot renew --dry-run
```

### 6.3 Убеждаемся что работает systemd timer (не cron)

```bash
systemctl list-timers | grep certbot
# → certbot.timer  active (waiting) — ОК

# Удаляем дублирующий cron если появился:
crontab -l 2>/dev/null | grep -v certbot | crontab -
```

### 6.4 Хук graceful reload при обновлении

```bash
cat > /etc/letsencrypt/renewal-hooks/post/reload.sh << 'EOF'
#!/bin/bash
# Nginx: graceful reload без дропа соединений
systemctl reload nginx

# Xray: SIGUSR1 = перечитать TLS сертификаты без рестарта (не теряет соединения)
docker kill --signal=SIGUSR1 remnawave-node 2>/dev/null \
  || docker restart remnawave-node 2>/dev/null \
  || true

logger "certbot: renewed, nginx reloaded, xray signaled"
EOF

chmod +x /etc/letsencrypt/renewal-hooks/post/reload.sh
```

---

## 7. Nginx — stream + HTTP

### 7.1 Проверка stream-модуля

```bash
nginx -V 2>&1 | grep -o 'with-stream'
# → with-stream  ← обязательно должно быть
```

### 7.2 Главный nginx.conf

```bash
rm -f /etc/nginx/sites-enabled/temp-acme

cat > /etc/nginx/nginx.conf << 'EOF'
user www-data;
worker_processes 1;               # 1 CPU → 1 воркер
worker_rlimit_nofile 100000;
error_log /var/log/nginx/error.log warn;
pid /run/nginx.pid;

events {
    worker_connections 10000;
    use epoll;
    multi_accept on;
}

# ═══════════════════════════════════════════════════════════════════════
# STREAM — TCP/443 passthrough с SNI routing (TLS не расшифровывается!)
# ═══════════════════════════════════════════════════════════════════════
stream {
    log_format stream_log '$remote_addr [$time_local] $protocol $status '
                          '$bytes_sent $bytes_received $session_time '
                          '"$ssl_preread_server_name"';
    access_log /var/log/nginx/stream.log stream_log;

    # SNI routing: Xray для наших доменов, фейк-сайт для всего остального
    map $ssl_preread_server_name $backend {
        main.your.domain   xray_reality;   # VLESS TCP REALITY
        cdn.your.domain    xray_xhttp;     # VLESS XHTTP
        default            nginx_https;    # Фейк для чужого SNI / без SNI
    }

    upstream xray_reality  { server 127.0.0.1:20001; }
    upstream xray_xhttp    { server 127.0.0.1:20002; }
    upstream nginx_https   { server 127.0.0.1:8443; }

    server {
        listen 443 reuseport;
        listen [::]:443 reuseport;
        ssl_preread on;              # Читаем SNI, НЕ расшифровываем
        proxy_pass $backend;
        proxy_connect_timeout 10s;
        proxy_timeout 3600s;         # 1 час: idle VPN-сессия не убьётся
        proxy_buffer_size 16k;
    }
}

# ═══════════════════════════════════════════════════════════════════════
# HTTP — сайты, certbot, rate limiting
# ═══════════════════════════════════════════════════════════════════════
http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    server_tokens off;

    log_format main '$remote_addr - [$time_local] "$request" $status '
                    '$body_bytes_sent "$http_user_agent"';
    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    keepalive_requests 1000;
    client_max_body_size 10m;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/json application/javascript
               application/xml text/xml image/svg+xml;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=site_req:10m rate=30r/s;
    limit_conn_zone $binary_remote_addr zone=site_conn:10m;

    include /etc/nginx/sites-enabled/*;
}
EOF
```

### 7.3 Виртуальные хосты

```bash
# Замените main.your.domain и cdn.your.domain на реальные домены!

cat > /etc/nginx/sites-available/vpn-server << 'NGINXEOF'
# ═══════════════════════════════════════════════════════════════════════
# :80 — certbot ACME + редирект на HTTPS
# ═══════════════════════════════════════════════════════════════════════
server {
    listen 80;
    listen [::]:80;
    server_name main.your.domain cdn.your.domain;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}

# ═══════════════════════════════════════════════════════════════════════
# :8443 — HTTPS фейк-сайт для stream (чужой SNI / без SNI)
# Nginx сам терминирует TLS → отдаёт сайт (HTTP/2)
# Только через stream proxy (127.0.0.1 only)
# ═══════════════════════════════════════════════════════════════════════
server {
    listen 127.0.0.1:8443 ssl;
    http2 on;
    server_name main.your.domain cdn.your.domain _;

    ssl_certificate     /etc/letsencrypt/live/main.your.domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/main.your.domain/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/main.your.domain/chain.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL_8443:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 1.1.1.1 8.8.8.8 valid=300s;
    resolver_timeout 5s;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'" always;

    root /var/www/main-site;
    index index.html;

    limit_req zone=site_req burst=60 nodelay;
    limit_conn site_conn 20;

    location / { try_files $uri $uri/ =404; }
    location ~* \.(css|js|jpg|jpeg|png|gif|ico|woff2|svg|xml|txt)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    error_page 404 /404.html;
    access_log /var/log/nginx/main-site.log main;
}

# ═══════════════════════════════════════════════════════════════════════
# :8080 — plain HTTP для Xray XHTTP fallback
# Xray принимает TLS, расшифровывает, не-XHTTP запрос → сюда
# Только с localhost
# ═══════════════════════════════════════════════════════════════════════
server {
    listen 127.0.0.1:8080;
    server_name cdn.your.domain;

    root /var/www/cdn-site;
    index index.html;

    limit_req zone=site_req burst=100 nodelay;

    location / { try_files $uri $uri/ =404; }
    location ~* \.(css|js|jpg|png|ico|woff2|svg|xml|txt)$ { expires 30d; }
    error_page 404 /404.html;
    access_log /var/log/nginx/xray-fallback.log main;
}
NGINXEOF

ln -sf /etc/nginx/sites-available/vpn-server /etc/nginx/sites-enabled/vpn-server
nginx -t && systemctl reload nginx

# Проверка:
ss -tlnp | grep nginx
# Ожидаем: 0.0.0.0:80, 0.0.0.0:443, 127.0.0.1:8080, 127.0.0.1:8443
```

---

## 8. Генерация криптографических ключей

```bash
# Временная установка Xray для генерации ключей
mkdir -p /tmp/xray-keygen && cd /tmp/xray-keygen
XRAY_VER=$(curl -s https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -r .tag_name)
wget -q "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VER}/Xray-linux-64.zip"
unzip -q Xray-linux-64.zip && chmod +x xray

# Сохраняем ВСЕ ключи в защищённый файл
mkdir -p /opt/remnawave-node/keys
cat > /opt/remnawave-node/keys/secrets.txt << 'EOF'
# ЗАПОЛНИТЕ ВЫВОД КОМАНД НИЖЕ
EOF
chmod 600 /opt/remnawave-node/keys/secrets.txt
```

### 8.1 REALITY X25519

```bash
echo "=== REALITY X25519 ==="
./xray x25519
# PrivateKey: XXXXXXXX → сервер (realitySettings.privateKey)
# PublicKey:  XXXXXXXX → клиент (realitySettings.password)
```

### 8.2 ML-DSA-65 (пост-квантовая подпись)

```bash
echo "=== ML-DSA-65 ==="
./xray mldsa65
# Seed:   XXXXXXXX → сервер (realitySettings.mldsa65Seed)
# Verify: XXXXXXXX → клиент (realitySettings.mldsa65Verify)
```

### 8.3 ShortIds (несколько для ротации)

```bash
echo "=== ShortIds ==="
for i in 1 2 3; do
  echo "ShortId $i: $(openssl rand -hex 8)"
done
```

### 8.4 Hysteria2 пароли

```bash
echo "=== Hysteria2 ==="
echo "Auth:       $(openssl rand -base64 24)"
echo "Salamander: $(openssl rand -base64 24)"
# Auth и Salamander — РАЗНЫЕ пароли!
```

### 8.5 Проверка target для REALITY

```bash
./xray tls ping www.microsoft.com
# Ожидаем:
# TLSVersion: tls1.3
# ALPN: h2
# CertSize: > 3500  ← нужно для mldsa65

cd ~ && rm -rf /tmp/xray-keygen
```

---

## 9. Docker и remnawave-node

### 9.1 Docker

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker
docker --version   # Docker version 27.x — ОК
```

### 9.2 Структура директорий

```bash
mkdir -p /opt/remnawave-node/{data,ssl/main,ssl/cdn}
cd /opt/remnawave-node

# Копируем сертификаты в папку для Docker (read-only mount):
cp /etc/letsencrypt/live/main.your.domain/fullchain.pem ssl/main/fullchain.pem
cp /etc/letsencrypt/live/main.your.domain/privkey.pem   ssl/main/privkey.pem
cp /etc/letsencrypt/live/cdn.your.domain/fullchain.pem  ssl/cdn/fullchain.pem
cp /etc/letsencrypt/live/cdn.your.domain/privkey.pem    ssl/cdn/privkey.pem
chmod 644 ssl/*/fullchain.pem && chmod 600 ssl/*/privkey.pem

# Хук для копирования при обновлении сертификатов:
cat > /etc/letsencrypt/renewal-hooks/deploy/copy-to-xray.sh << 'HOOK'
#!/bin/bash
cp /etc/letsencrypt/live/main.your.domain/fullchain.pem /opt/remnawave-node/ssl/main/fullchain.pem
cp /etc/letsencrypt/live/main.your.domain/privkey.pem   /opt/remnawave-node/ssl/main/privkey.pem
cp /etc/letsencrypt/live/cdn.your.domain/fullchain.pem  /opt/remnawave-node/ssl/cdn/fullchain.pem
cp /etc/letsencrypt/live/cdn.your.domain/privkey.pem    /opt/remnawave-node/ssl/cdn/privkey.pem
chmod 644 /opt/remnawave-node/ssl/*/fullchain.pem
chmod 600 /opt/remnawave-node/ssl/*/privkey.pem
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/copy-to-xray.sh
```

### 9.3 docker-compose.yml

> **Примечания к источникам (исправления):**  
> — `APP_PORT` → `NODE_PORT` (переименовано в новых версиях remnawave-node)  
> — `APP_SECRET` → `SECRET_KEY`  
> — `version: '3.8'` удалён (устарел и игнорируется в Compose v2)  
> — добавлен `cap_add: - NET_ADMIN` (требуется с v2.6.0+ для управления iptables/плагинов)

```bash
cat > /opt/remnawave-node/docker-compose.yml << 'EOF'
services:
  remnawave-node:
    image: remnawave/node:latest
    container_name: remnawave-node
    restart: unless-stopped
    network_mode: host

    environment:
      # NODE_PORT — порт для связи с панелью remnawave
      # Примечание: был APP_PORT в старых версиях, сейчас NODE_PORT
      - NODE_PORT=2095
      # SECRET_KEY — JWT-секрет для связи с панелью
      # Примечание: был APP_SECRET в старых версиях
      - SECRET_KEY=ЗАМЕНИТЕ_НА_СЛУЧАЙНУЮ_СТРОКУ_openssl_rand_hex_32
      - LOG_LEVEL=warn

    volumes:
      - ./data:/var/lib/remnawave
      - ./ssl:/etc/ssl/xray:ro

    ulimits:
      nofile:
        soft: 500000
        hard: 500000

    # NET_ADMIN нужен для управления iptables при port hopping (с v2.6.0+)
    cap_add:
      - NET_ADMIN

    mem_swappiness: 0

    deploy:
      resources:
        limits:
          memory: 1536M
        reservations:
          memory: 256M

    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"
EOF

# Сгенерировать SECRET_KEY:
echo "SECRET_KEY=$(openssl rand -hex 32)"
# Вставьте результат в docker-compose.yml вместо ЗАМЕНИТЕ_НА_...
```

### 9.4 Блокировка NODE_PORT снаружи

```bash
# network_mode: host → контейнер биндится на 0.0.0.0, минуя UFW
# Без iptables правила порт 2095 будет виден снаружи!

# Применяем прямо сейчас:
iptables -C ufw-before-input -p tcp --dport 2095 ! -s 127.0.0.1 -j DROP 2>/dev/null \
  || iptables -I ufw-before-input -p tcp --dport 2095 ! -s 127.0.0.1 -j DROP

# Сохраняем в before.rules (переживает ребут + ufw reload):
awk '/^COMMIT$/ && !done{
    print "-A ufw-before-input -p tcp --dport 2095 ! -s 127.0.0.1 -j DROP"
    done=1
} 1' /etc/ufw/before.rules > /tmp/ufw_br.tmp \
  && mv /tmp/ufw_br.tmp /etc/ufw/before.rules

# Проверка:
grep '2095' /etc/ufw/before.rules
# → -A ufw-before-input -p tcp --dport 2095 ! -s 127.0.0.1 -j DROP
```

### 9.5 Запуск

```bash
cd /opt/remnawave-node
docker compose up -d
docker compose logs -f --tail=50
# Убеждаемся что Xray стартовал без ошибок
```

---

## 10. Xray — конфигурация трёх протоколов

В панели remnawave создаём ноду и настраиваем три inbound. Ниже — JSON-конфиги для каждого.

### 10.1 Inbound 1: VLESS TCP REALITY + Vision (selfsteal)

```json
{
  "tag": "VLESS-TCP-REALITY",
  "port": 20001,
  "listen": "0.0.0.0",
  "protocol": "vless",
  "settings": {
    "clients": [],
    "decryption": "none"
  },
  "streamSettings": {
    "network": "raw",
    "security": "reality",
    "realitySettings": {
      "show": false,
      "target": "127.0.0.1:8443",
      "serverNames": [
        "main.your.domain"
      ],
      "privateKey": "ВАШ_REALITY_PRIVATE_KEY",
      "shortIds": [
        "ВАШ_SHORTID_1",
        "ВАШ_SHORTID_2",
        "ВАШ_SHORTID_3"
      ],
      "mldsa65Seed": "ВАШ_MLDSA65_SEED",
      "maxTimeDiff": 60000,
      "limitFallbackUpload": {
        "afterBytes": 10485760,
        "bytesPerSec": 786432,
        "burstBytesPerSec": 3145728
      },
      "limitFallbackDownload": {
        "afterBytes": 52428800,
        "bytesPerSec": 1572864,
        "burstBytesPerSec": 7340032
      }
    },
    "rawSettings": {}
  },
  "sniffing": {
    "enabled": true,
    "destOverride": ["http", "tls", "quic"],
    "routeOnly": false
  }
}
```

> **flow: xtls-rprx-vision** указывается на уровне пользователя в панели remnawave, не в inbound JSON.  
> **Selfsteal:** target `127.0.0.1:8443` = Nginx с реальным сайтом → зондировщик ТСПУ получает настоящий HTML вашего сайта.

### 10.2 Inbound 2: VLESS XHTTP (через CDN)

```json
{
  "tag": "VLESS-XHTTP",
  "port": 20002,
  "listen": "0.0.0.0",
  "protocol": "vless",
  "settings": {
    "clients": [],
    "decryption": "none"
  },
  "streamSettings": {
    "network": "xhttp",
    "security": "tls",
    "tlsSettings": {
      "alpn": ["h2"],
      "minVersion": "1.3",
      "maxVersion": "1.3",
      "certificates": [
        {
          "certificateFile": "/etc/ssl/xray/cdn/fullchain.pem",
          "keyFile": "/etc/ssl/xray/cdn/privkey.pem"
        }
      ]
    },
    "xhttpSettings": {
      "host": "cdn.your.domain",
      "path": "/api/v1/sync",
      "mode": "packet-up",
      "extra": {
        "xPaddingBytes": "150-1500",
        "xPaddingHeader": "X-Request-ID",
        "xPaddingKey": "rid",
        "xPaddingObfsMode": true,
        "xPaddingPlacement": "queryInHeader",
        "sessionPlacement": "header",
        "sessionKey": "X-Session",
        "seqPlacement": "header",
        "seqKey": "X-Seq",
        "uplinkHTTPMethod": "POST",
        "scMaxEachPostBytes": "400000-1200000",
        "scMinPostsIntervalMs": "20-60",
        "scMaxBufferedPosts": 30,
        "noGRPCHeader": false,
        "noSSEHeader": false,
        "headers": {
          "Accept": "application/json, text/plain, */*",
          "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
          "Cache-Control": "no-cache",
          "Pragma": "no-cache",
          "Origin": "https://cdn.your.domain"
        },
        "xmux": {
          "maxConcurrency": "8-16",
          "cMaxReuseTimes": "64-128",
          "hMaxRequestTimes": "400-700",
          "hMaxReusableSecs": "1800-3000",
          "hKeepAlivePeriod": 25
        }
      }
    }
  },
  "sniffing": {
    "enabled": true,
    "destOverride": ["http", "tls", "quic"]
  }
}
```

> **Fallback:** при невалидном запросе можно добавить `"fallbacks": [{"dest": "127.0.0.1:8080"}]` в `settings` для перенаправления на фейковый сайт.

### 10.3 Inbound 3: Hysteria2

> **Примечания:**  
> — `finalmask.quicParams` — правильное место для congestion/udpHop начиная с **v26.3.27**. До этого они были в `hysteriaSettings` (legacy, выдаёт warning).  
> — Salamander в Xray **несовместим** с официальным hysteria2-сервером/клиентом (Issue #5712). Работает только между двумя Xray-инстансами.  
> — При использовании Salamander masquerade **отключается** автоматически (несовместимы).  
> — QUIC window sizes: дефолт Xray (8MB/20MB) создаёт fingerprint. Chrome использует **6MB/15MB** — используем это.

**Вариант A — с masquerade (рекомендуется при неблокированном QUIC):**

```json
{
  "tag": "HYSTERIA2",
  "port": 443,
  "listen": "0.0.0.0",
  "protocol": "hysteria",
  "settings": {
    "version": 2,
    "clients": []
  },
  "streamSettings": {
    "network": "hysteria",
    "security": "tls",
    "tlsSettings": {
      "alpn": ["h3"],
      "certificates": [
        {
          "certificateFile": "/etc/ssl/xray/main/fullchain.pem",
          "keyFile": "/etc/ssl/xray/main/privkey.pem"
        }
      ]
    },
    "hysteriaSettings": {
      "version": 2,
      "udpIdleTimeout": 60,
      "masquerade": {
        "type": "proxy",
        "url": "https://www.bing.com",
        "rewriteHost": true,
        "insecure": false
      }
    },
    "finalmask": {
      "quicParams": {
        "congestion": "bbr",
        "bbrProfile": "standard",
        "initStreamReceiveWindow": 6291456,
        "maxStreamReceiveWindow": 6291456,
        "initConnectionReceiveWindow": 15728640,
        "maxConnectionReceiveWindow": 15728640,
        "maxIdleTimeout": 30,
        "keepAlivePeriod": 10,
        "maxIncomingStreams": 1024,
        "disablePathMTUDiscovery": false,
        "udpHop": {
          "ports": "20000-50000",
          "interval": "5-15"
        }
      }
    }
  }
}
```

**Вариант B — с Salamander (при заблокированном QUIC; только Xray↔Xray):**

```json
{
  "tag": "HYSTERIA2-SALAMANDER",
  "port": 443,
  "listen": "0.0.0.0",
  "protocol": "hysteria",
  "settings": {
    "version": 2,
    "clients": []
  },
  "streamSettings": {
    "network": "hysteria",
    "security": "tls",
    "tlsSettings": {
      "alpn": ["h3"],
      "certificates": [
        {
          "certificateFile": "/etc/ssl/xray/main/fullchain.pem",
          "keyFile": "/etc/ssl/xray/main/privkey.pem"
        }
      ]
    },
    "hysteriaSettings": {
      "version": 2,
      "udpIdleTimeout": 60
    },
    "finalmask": {
      "udp": [
        {
          "type": "salamander",
          "settings": {
            "password": "ВАШ_SALAMANDER_PASSWORD"
          }
        }
      ],
      "quicParams": {
        "congestion": "brutal",
        "brutalUp": "100 mbps",
        "brutalDown": "300 mbps",
        "initStreamReceiveWindow": 6291456,
        "maxStreamReceiveWindow": 6291456,
        "initConnectionReceiveWindow": 15728640,
        "maxConnectionReceiveWindow": 15728640,
        "maxIdleTimeout": 30,
        "keepAlivePeriod": 10,
        "maxIncomingStreams": 1024,
        "udpHop": {
          "ports": "20000-50000",
          "interval": "5-15"
        }
      }
    }
  }
}
```

> **Congestion control:**  
> — `bbr` — неотличим от браузерного HTTP/3, рекомендуется при стабильном канале  
> — `brutal` — проламывает throttling, нужны верные `brutalUp/brutalDown` (~80% реальной скорости)  
> — `force-brutal` — принудительно без согласования, для экстремального throttling

### 10.4 Outbounds и Routing (добавить к конфигу)

```json
{
  "outbounds": [
    {
      "tag": "DIRECT",
      "protocol": "freedom",
      "settings": {}
    },
    {
      "tag": "BLOCK",
      "protocol": "blackhole",
      "settings": {}
    }
  ],
  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      {
        "type": "field",
        "ip": ["geoip:private"],
        "outboundTag": "BLOCK"
      },
      {
        "type": "field",
        "domain": ["geosite:private"],
        "outboundTag": "BLOCK"
      },
      {
        "type": "field",
        "protocol": ["bittorrent"],
        "outboundTag": "BLOCK"
      },
      {
        "type": "field",
        "network": "tcp,udp",
        "outboundTag": "DIRECT"
      }
    ]
  }
}
```

---

## 11. iptables для Hysteria2 Port Hopping

В Xray-inbound (в отличие от официального hysteria2) нет автоматической настройки firewall для диапазона портов — настраиваем вручную:

```bash
# Получаем имя интерфейса:
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')

# Весь UDP из диапазона 20000-50000 → перенаправить на :443 где слушает Xray
iptables -t nat -A PREROUTING -i $IFACE -p udp --dport 20000:50000 -j REDIRECT --to-ports 443
ip6tables -t nat -A PREROUTING -i $IFACE -p udp --dport 20000:50000 -j REDIRECT --to-ports 443

# INPUT rule для диапазона:
iptables -A INPUT -p udp --dport 20000:50000 -j ACCEPT
ip6tables -A INPUT -p udp --dport 20000:50000 -j ACCEPT

# Сохраняем (переживают ребут):
iptables-save  > /etc/iptables/rules.v4
ip6tables-save > /etc/iptables/rules.v6

# Проверка:
iptables -t nat -L PREROUTING -n --line-numbers | grep udp
# → REDIRECT  udp  -- 0.0.0.0/0  0.0.0.0/0  udp dpts:20000:50000  redir ports 443
```

---

## 12. CDN для XHTTP

### Выбор CDN (для обхода российских блокировок)

| Провайдер | Поддержка РФ | HTTPS-to-origin | Рекомендация |
|---|---|---|---|
| G-Core Labs (gcore.com) | ✅ | Full SSL | ⭐⭐⭐⭐⭐ |
| Edgecenter.ru | ✅ | Full SSL | ⭐⭐⭐⭐ |
| Selectel CDN | ✅ | Full SSL | ⭐⭐⭐⭐ |
| Cloudflare | Частично | Full (Strict) | ⭐⭐⭐ |

### Настройка G-Core Labs (пример)

1. Зарегистрируйтесь на `gcore.com` → CDN Resources → Create
2. Origin: `https://cdn.your.domain:443`
3. Origin SSL: **Full (Strict)**
4. Получите CNAME вида `*.gcdn.co`
5. В DNS замените A-запись:
   ```
   cdn.your.domain  CNAME  your-resource.gcdn.co
   ```
6. Включите HTTP/2, выключите Minify и Auto Optimize

### Проверка CDN

```bash
curl -v --http2 \
  -H "X-Session: test" \
  -H "X-Seq: 0" \
  "https://cdn.your.domain/api/v1/sync" \
  -d "test" 2>&1 | head -30
# Ожидаем: HTTP 400 или 200 от Xray (не Connection refused)
```

---

## 13. Проверки

### 13.1 Порты снаружи

```bash
# Запускать с ДРУГОЙ машины или через https://nmap.online
nmap -Pn -p 22,80,443,2095,8080,8443,20001,20002 IP_СЕРВЕРА

# Ожидаемый результат:
# 22/tcp    filtered  ssh      ← стена (knockd)
# 80/tcp    open      http     ← ОК
# 443/tcp   open      https    ← ОК
# 2095/tcp  filtered  -        ← ОК (iptables)
# 8080/tcp  filtered  -        ← ОК (только localhost)
# 8443/tcp  filtered  -        ← ОК (только localhost)
# 20001/tcp filtered  -        ← ОК (только localhost)
# 20002/tcp filtered  -        ← ОК (только localhost)
```

### 13.2 TLS и сайты

```bash
# TLS версия и тип ключа:
openssl s_client -connect main.your.domain:443 -servername main.your.domain 2>/dev/null \
  | openssl x509 -noout -text | grep -E 'Protocol|Public Key|OID'
# → Protocol: TLSv1.3, Public Key: 256 bit (ECDSA P-256)

# Сайт отдаёт HTML без VPN-маркеров:
curl -sI https://main.your.domain | grep -iE 'xray|proxy|vless|vmess|hysteria'
# → пусто

# Active probing: чужой SNI получает ответ как от обычного сервера:
openssl s_client -connect IP_СЕРВЕРА:443 -servername example.com 2>/dev/null | head -5
# → получает TLS ответ (не RST)
```

### 13.3 BBR активен

```bash
sysctl net.ipv4.tcp_congestion_control
# → bbr

ss -tni | grep -m3 'bbr\|cubic'
# → cwnd=... bbr  ← должно быть bbr
```

### 13.4 Hysteria2 UDP доступен

```bash
nmap -sU -p 443 IP_СЕРВЕРА
# → 443/udp open|filtered  ← ОК
```

### 13.5 Knock работает

```bash
# 1. SSH должен быть недоступен:
nc -zv -w3 IP_СЕРВЕРА 22
# → Connection timed out

# 2. Knock + SSH:
knock IP_СЕРВЕРА K1 K2 K3 -d 150 && sleep 1 && ssh -i ~/.ssh/vpn_server_key root@IP_СЕРВЕРА
# → успешно

# 3. После 30 сек снова закрыт:
nc -zv -w3 IP_СЕРВЕРА 22
# → filtered
```

### 13.6 Hysteria2 + QUIC windows

```bash
docker logs remnawave-node 2>&1 | grep -iE 'hysteria|quic|inbound|started'
# → listening hysteria on 0.0.0.0:443
```

---

## 14. Клиентские конфигурации

### 14.1 VLESS TCP REALITY + Vision

```json
{
  "outbounds": [{
    "protocol": "vless",
    "settings": {
      "vnext": [{
        "address": "main.your.domain",
        "port": 443,
        "users": [{
          "id": "ВАШ_UUID_ИЗ_ПАНЕЛИ",
          "encryption": "none",
          "flow": "xtls-rprx-vision"
        }]
      }]
    },
    "streamSettings": {
      "network": "raw",
      "security": "reality",
      "realitySettings": {
        "serverName": "main.your.domain",
        "fingerprint": "chrome",
        "password": "ВАШ_REALITY_PUBLIC_KEY",
        "shortId": "ВАШ_SHORTID",
        "mldsa65Verify": "ВАШ_MLDSA65_VERIFY",
        "spiderX": "/about.html"
      }
    },
    "mux": { "enabled": false }
  }]
}
```

> **fingerprint:** при блокировках попробуйте `firefox`, `qq`, `safari`.  
> **mux:** отключён — Vision + mux несовместимы.

### 14.2 VLESS XHTTP через CDN

```json
{
  "outbounds": [{
    "protocol": "vless",
    "settings": {
      "vnext": [{
        "address": "cdn.your.domain",
        "port": 443,
        "users": [{
          "id": "ВАШ_UUID_ИЗ_ПАНЕЛИ",
          "encryption": "none"
        }]
      }]
    },
    "streamSettings": {
      "network": "xhttp",
      "security": "tls",
      "tlsSettings": {
        "serverName": "cdn.your.domain",
        "fingerprint": "chrome",
        "alpn": ["h2"],
        "minVersion": "1.3"
      },
      "xhttpSettings": {
        "host": "cdn.your.domain",
        "path": "/api/v1/sync",
        "mode": "packet-up",
        "extra": {
          "xPaddingBytes": "150-1500",
          "xPaddingHeader": "X-Request-ID",
          "xPaddingKey": "rid",
          "sessionKey": "X-Session",
          "seqKey": "X-Seq",
          "headers": {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            "Cache-Control": "no-cache"
          },
          "xmux": {
            "maxConcurrency": "8-16",
            "cMaxReuseTimes": "64-128",
            "hMaxRequestTimes": "400-700",
            "hMaxReusableSecs": "1800-3000",
            "hKeepAlivePeriod": 25
          }
        }
      }
    }
  }]
}
```

### 14.3 Hysteria2 (с masquerade, Вариант A)

```json
{
  "outbounds": [{
    "tag": "hy2-out",
    "protocol": "hysteria",
    "settings": {
      "version": 2,
      "address": "main.your.domain",
      "port": 443
    },
    "streamSettings": {
      "network": "hysteria",
      "security": "tls",
      "tlsSettings": {
        "serverName": "main.your.domain",
        "fingerprint": "chrome",
        "alpn": ["h3"]
      },
      "hysteriaSettings": {
        "version": 2,
        "auth": "ВАШ_HY2_AUTH_PASSWORD",
        "udpIdleTimeout": 60
      },
      "finalmask": {
        "quicParams": {
          "congestion": "bbr",
          "keepAlivePeriod": 10,
          "maxIdleTimeout": 30,
          "initStreamReceiveWindow": 6291456,
          "maxStreamReceiveWindow": 6291456,
          "initConnectionReceiveWindow": 15728640,
          "maxConnectionReceiveWindow": 15728640,
          "udpHop": {
            "ports": "20000-50000",
            "interval": "5-15"
          }
        }
      }
    }
  }]
}
```

### 14.4 Hysteria2 (с Salamander, Вариант B — только Xray↔Xray)

```json
{
  "outbounds": [{
    "protocol": "hysteria",
    "settings": {
      "version": 2,
      "address": "main.your.domain",
      "port": 443
    },
    "streamSettings": {
      "network": "hysteria",
      "security": "tls",
      "tlsSettings": {
        "serverName": "main.your.domain",
        "fingerprint": "chrome",
        "alpn": ["h3"]
      },
      "hysteriaSettings": {
        "version": 2,
        "auth": "ВАШ_HY2_AUTH_PASSWORD",
        "udpIdleTimeout": 60
      },
      "finalmask": {
        "udp": [
          {
            "type": "salamander",
            "settings": {
              "password": "ВАШ_SALAMANDER_PASSWORD"
            }
          }
        ],
        "quicParams": {
          "congestion": "brutal",
          "brutalUp": "100 mbps",
          "brutalDown": "300 mbps",
          "keepAlivePeriod": 10,
          "maxIdleTimeout": 30,
          "initStreamReceiveWindow": 6291456,
          "maxStreamReceiveWindow": 6291456,
          "initConnectionReceiveWindow": 15728640,
          "maxConnectionReceiveWindow": 15728640,
          "udpHop": {
            "ports": "20000-50000",
            "interval": "5-15"
          }
        }
      }
    }
  }]
}
```

---

## 15. Скрипт быстрой проверки сервера

```bash
cat > /usr/local/bin/srv-check << 'EOF'
#!/bin/bash
echo "═══════════════════════════════════════════════"
echo " $(date '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════"

echo ""
echo "[ СЕРВИСЫ ]"
systemctl is-active nginx    &>/dev/null && echo "✅ Nginx" || echo "❌ Nginx DOWN"
systemctl is-active knockd   &>/dev/null && echo "✅ knockd" || echo "❌ knockd DOWN"
systemctl is-active fail2ban &>/dev/null && echo "✅ fail2ban" || echo "❌ fail2ban DOWN"
docker inspect -f '{{.State.Status}}' remnawave-node 2>/dev/null \
  | grep -q running && echo "✅ remnawave-node" || echo "❌ remnawave-node DOWN"

echo ""
echo "[ ПОРТЫ ]"
ss -tlnp | awk 'NR>1 {print $4}' | grep -E ':80$|:443$|:8080$|:8443$|:20001$|:20002$'
ss -ulnp | awk 'NR>1 {print $4}' | grep ':443$' && echo "UDP/443 слушает (Hysteria2)"

echo ""
echo "[ NODE_PORT 2095 ]"
timeout 2 nc -z 127.0.0.1 2095 2>/dev/null \
  && echo "✅ remnawave API доступен локально" \
  || echo "⚠️ remnawave API не отвечает"
iptables -C ufw-before-input -p tcp --dport 2095 ! -s 127.0.0.1 -j DROP 2>/dev/null \
  && echo "✅ Порт 2095 заблокирован снаружи" \
  || echo "❌ Порт 2095 НЕ заблокирован снаружи!"

echo ""
echo "[ TIME_WAIT (нагрузка XHTTP) ]"
TW=$(ss -tn state time-wait 2>/dev/null | wc -l)
EST=$(ss -tn state established 2>/dev/null | wc -l)
echo "TIME_WAIT: $TW | ESTABLISHED: $EST"
[ "$TW" -gt 100000 ] && echo "⚠️ TIME_WAIT высокий!"

echo ""
echo "[ ПАМЯТЬ ]"
free -h | grep -E 'Mem|Swap'
docker stats --no-stream --format "Docker: {{.MemUsage}} CPU: {{.CPUPerc}}" \
  remnawave-node 2>/dev/null

echo ""
echo "[ СЕРТИФИКАТ ]"
CERT_EXP=$(openssl s_client -connect main.your.domain:443 \
  -servername main.your.domain 2>/dev/null \
  | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
echo "Истекает: $CERT_EXP"

echo ""
echo "[ BBR ]"
sysctl -n net.ipv4.tcp_congestion_control
sysctl -n net.core.default_qdisc

echo ""
echo "[ FAIL2BAN ]"
fail2ban-client status 2>/dev/null | grep 'Jail list'

echo "═══════════════════════════════════════════════"
EOF

chmod +x /usr/local/bin/srv-check
srv-check
```

---

## 16. Мониторинг и обслуживание

### Полезные команды

```bash
# Подключение к серверу:
knock IP_СЕРВЕРА K1 K2 K3 -d 150 && sleep 1 && ssh -i ~/.ssh/vpn_server_key root@IP_СЕРВЕРА

# Статус сервера:
srv-check

# Логи remnawave:
docker logs remnawave-node --tail 100 -f

# Логи nginx stream (SNI routing):
tail -f /var/log/nginx/stream.log

# TIME_WAIT под нагрузкой:
watch -n2 'echo "TW: $(ss -tn state time-wait | wc -l)  EST: $(ss -tn state established | wc -l)"'

# Обновление Xray (через Docker):
cd /opt/remnawave-node
docker compose pull
docker compose up -d
docker exec remnawave-node xray version

# Certbot:
certbot certificates
systemctl status certbot.timer

# Knock-последовательность (если забыл):
cat /root/.knock_sequence
```

### Ротация shortId (при подозрении на компрометацию)

```bash
NEW_ID=$(openssl rand -hex 8)
echo "Новый shortId: $NEW_ID"
# Добавить в массив shortIds в конфиге ноды remnawave
# Обновить клиентские профили
# Старый shortId можно удалить через несколько дней
docker restart remnawave-node
```

### Crontab

```bash
(crontab -l 2>/dev/null; cat << 'CRON'
# Чистим неиспользуемые Docker-образы раз в неделю
0 3 * * 0 docker image prune -f >> /var/log/docker-prune.log 2>&1

# Еженедельный отчёт в syslog
0 9 * * 1 /usr/local/bin/srv-check | logger -t srv-check
CRON
) | crontab -
```

---

## 17. Устранение проблем

### ❌ REALITY: handshake failed

```bash
timedatectl status | grep synchronized   # Должно быть yes
curl -k https://127.0.0.1:8443/ -H "Host: main.your.domain"  # Selfsteal сайт доступен?
# Проверить fingerprint в клиенте: не должен быть пустым или "unsafe"
# Попробовать fingerprint: firefox, qq, safari, ios
```

### ❌ Hysteria2: не подключается

```bash
docker logs remnawave-node 2>&1 | grep -iE 'hysteria|443|error'
iptables -t nat -L PREROUTING -n | grep udp    # Port hopping настроен?
ss -ulnp | grep ':443'                          # Xray слушает UDP/443?
# При Salamander — убедитесь что клиент тоже Xray (не официальный hysteria2 клиент)
```

### ❌ XHTTP через CDN: Connection reset

```bash
ss -tlnp | grep 20002           # Xray слушает?
curl -k https://127.0.0.1:20002/ -H "Host: cdn.your.domain"  # Напрямую работает?
# Проверить в CDN: Origin SSL = Full (Strict), HTTP/2 включён
```

### ❌ Порт 2095 виден снаружи

```bash
iptables -L ufw-before-input -n | grep 2095
# Если пусто — переприменить правило:
iptables -I ufw-before-input -p tcp --dport 2095 ! -s 127.0.0.1 -j DROP
```

### ❌ Nginx не стартует: Address already in use :443

```bash
ss -tlnp | grep :443   # Кто занял порт?
docker stop remnawave-node
systemctl restart nginx
docker start remnawave-node
```

### Блокировка в конкретном регионе

```
VLESS TCP REALITY:
  → сменить fingerprint: chrome → firefox → qq → safari
  → проверить IP/ASN соответствие: curl ipinfo.io/json

XHTTP:
  → сменить path (/api/v1/sync → /v2/stream, etc.)
  → сменить sessionKey, seqKey
  → попробовать mode: stream-up вместо packet-up

Hysteria2:
  → включить Salamander (если Xray↔Xray)
  → уменьшить brutalUp/brutalDown (100mbps → 50mbps)
  → проверить port hopping: iptables -t nat -L PREROUTING
```

---

## 18. Чеклист перед запуском

```
□ DNS: три A-записи настроены и пропагировались
□ SSH-ключ скопирован, вход по ключу проверен
□ Knock-последовательность сохранена надёжно
□ UFW включён: открыты 80/tcp, 443/tcp, 443/udp, 20000-50000/udp
□ knockd работает с отдельной цепочкой KNOCKD
□ Сертификаты ECDSA P-256 получены для обоих доменов
□ Nginx: stream слушает 443, HTTP слушает 80
□ Nginx: 8080 и 8443 доступны только с localhost
□ Nginx: 8443 отдаёт HTTP/2 с правильным сертификатом
□ Selfsteal сайт (main.your.domain): curl → HTML сайта, не ошибка
□ CDN сайт (cdn.your.domain): curl → HTML CDN-лендинга
□ remnawave-node запущен: docker ps
□ docker-compose: NODE_PORT (не APP_PORT), SECRET_KEY (не APP_SECRET), нет version:
□ Порт 2095 заблокирован снаружи (iptables, проверить через srv-check)
□ Xray VLESS TCP REALITY inbound на порту 20001 (listen 127.0.0.1 через nginx или 0.0.0.0)
□ Xray VLESS XHTTP inbound на порту 20002
□ Xray Hysteria2 inbound на UDP порту 443
□ finalmask.quicParams используется для congestion и udpHop (не hysteriaSettings)
□ QUIC windows: 6MB/15MB (Chrome-совместимые, не дефолтные 8MB/20MB)
□ iptables PREROUTING: UDP 20000-50000 → 443
□ allowInsecure отсутствует везде (удалён в v26.6.1)
□ nmap снаружи: только 80/443 open, остальное filtered
□ timedatectl: synchronized: yes (критично для REALITY)
□ sysctl: bbr + fq, buffers 16MB
□ Сертификаты скопированы в /opt/remnawave-node/ssl/ (deploy hook настроен)
□ srv-check проходит без ❌
```

---

## Примечания к источникам

| Файл | Проблема | Исправление |
|---|---|---|
| vpn-stealth-guide-2026.md | `APP_PORT=2053` | `NODE_PORT=2095` |
| vpn-stealth-guide-2026.md | `APP_SECRET=...` | `SECRET_KEY=...` |
| vpn-stealth-guide-2026.md | `version: '3.8'` в docker-compose | Удалено (игнорируется в Compose v2) |
| setup-guide-v4_1.md | `APP_PORT=2095` | `NODE_PORT=2095` |
| setup-guide-v4_1.md | `net.core.rmem_max=134217728` (128MB) | `16777216` (16MB) для 2 GB RAM |
| setup-guide-v4_1.md | `tcp_max_tw_buckets=524288` | Оставлено (уже исправлено от 2M в v3) |
| hysteria2-xray-dpi-report.md | Пример клиента с windows 16MB/41MB | Исправлено на Chrome-matching 6MB/15MB |
| Все источники | `hysteriaSettings.congestion` и `udpHop` | `finalmask.quicParams.*` (с v26.3.27) |
| Оба гайда | Отсутствует `cap_add: - NET_ADMIN` | Добавлено в docker-compose |
| vpn-stealth-guide-2026.md | Salamander + masquerade вместе | Несовместимы — только одно |

---

*Составлен на основе: setup-guide-v4.1, vpn-stealth-guide-2026, hysteria2-xray-dpi-report, VLESS XHTTP DPI research, VLESS TCP REALITY research, анализа исходников Xray-core v26.6.1 (июнь 2026)*
