# VPN-сервер: анализ и оптимизация — исследование 2026

> **Контекст:** Debian 12 / Ubuntu 22.04, 2 ядра / 2 GB RAM, 10 Gbps  
> **Протоколы:** VLESS TCP+REALITY, XHTTP (CDN, packet-up), Hysteria2  
> **Xray-core v26.6.1** в Docker с `network_mode: host`

---

## Итог по гайду

| Категория | Оценка | Детали |
|---|---|---|
| BBR + fq | ✅ Правильно | Но не BBR3 — упущена ~15% производительность |
| TCP буферы 16 MB | ✅ Правильно | Безопасно для 2 GB RAM, достаточно для QUIC |
| fq (не fq_codel) | ✅ Правильно | fq_codel дропает пакеты на отправке — плохо для серверов |
| QUIC windows 6/15 MB | ✅ Обоснованно | Chrome-matching, но ограничивает максимальный throughput |
| finalmask.quicParams | ✅ Правильно | Верное место с v26.3.27 |
| Salamander vs masquerade | ✅ Правильно | Несовместимы, выбрать одно |
| tx-udp-segmentation | ❌ Отсутствует | Критично для upload Hysteria2 |
| ethtool ring buffers | ❌ Отсутствует | Дропы пакетов при burst на VPS |
| netdev_budget | ⚠️ Отсутствует | Нужно поднять для 10 Gbps |
| tcp_no_metrics_save | ⚠️ Отсутствует | Стухшие метрики от старых соединений |
| tcp_slow_start_after_idle | ❌ Отсутствует | Снижает скорость VLESS TCP после паузы |
| CPU governor | ❌ Отсутствует | До +30% throughput если powersave активен |
| nginx worker_processes 1 | ⚠️ Неоптимально | На 2 ядрах должно быть 2 или auto |
| optmem_max | ❌ Отсутствует | Нужен для UDP GSO/GRO ancillary буферов |

---

## 1. BBR3 (XanMod) — пропущенный прирост

### Проблема

В гайде используется **BBR v1** (стандартный для Ubuntu 22.04 / Debian 12 ядер 5.15 / 6.1). Дистрибутивные ядра не содержат отдельного имени `bbr3` — только `bbr`. Это BBR первой версии.

**BBR v3** — это ноябрь 2024, IETF draft `draft-ietf-ccwg-bbr-04`. Ключевые улучшения относительно BBR v1:

- Исправлен starved flow problem (v1 несправедлив при конкуренции)
- Лучший probe bandwidth цикл (меньше microburst'ов)
- Более корректная работа с потерями — не путает congestion loss с random loss
- На высоких RTT и lossy каналах значительно лучше throughput

### Решение: XanMod kernel

XanMod — это ядро с патчами под Debian/Ubuntu, содержащее:
- **BBR v3** (тот же что в Google production)
- Google Multigenerational LRU (лучший memory pressure)
- Cloudflare TCP collapse processing
- Поставляется через APT-репозиторий

```bash
# Установка XanMod для Debian 12 / Ubuntu 22.04
# Шаг 1: добавить репозиторий
wget -qO - https://dl.xanmod.org/archive.key \
  | gpg --dearmor -o /usr/share/keyrings/xanmod-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/xanmod-archive-keyring.gpg] \
  http://deb.xanmod.org releases main" \
  | tee /etc/apt/sources.list.d/xanmod-release.list

apt update

# Узнать уровень оптимизации CPU:
awk -f <(wget -qO - https://dl.xanmod.org/check_x86-64_psabi.sh)
# → Результат покажет x86-64-v2 / v3 / v4 (для VPS обычно v2 или v3)

# Установить (замените v3 на ваш уровень):
apt install -y linux-xanmod-x64v3

# После перезагрузки:
uname -r
# → 6.x.y-xanmod1 (или похожее)

# Проверить BBR3:
sysctl net.ipv4.tcp_congestion_control
# → bbr (XanMod автоматически включает BBR3 — sysctl name те же)

# Убедиться что это BBR3, а не v1:
modinfo tcp_bbr | grep version
# или проверить по поведению:
ss -tni | grep bbr
```

> **⚠️ VPS риски:** XanMod требует отключённого Secure Boot. На некоторых VPS-провайдерах (например, Hetzner) это выключено по умолчанию. Проверьте перед установкой: `mokutil --sb-state`. Если Secure Boot активен — не устанавливайте.

> **Альтернатива без смены ядра:** Если XanMod неприемлем, `net.ipv4.tcp_congestion_control=bbr` + `net.core.default_qdisc=fq` уже хорошая конфигурация. BBR1 значительно лучше CUBIC.

---

## 2. tx-udp-segmentation — главный резерв upload

### Почему это важно для Hysteria2

Hysteria2 использует **quic-go** под капотом (через Xray). quic-go начиная с v0.36 автоматически использует **UDP GSO** (Generic Segmentation Offload) на Linux ≥ 4.18:

```
Без GSO:  sendmsg() × N пакетов = N системных вызовов
С GSO:    sendmsg() с 64KB буфером = 1 системный вызов → ядро режет на MTU-пакеты
```

Прирост: значительное снижение CPU-nагрузки, особенно на upload.

**tx-udp-segmentation** (`NETIF_F_GSO_UDP_L4`) — аппаратная разгрузка USO на NIC:
- Ядро отправляет «монстр-пакет» прямо в NIC
- NIC сам нарезает его на MTU-куски
- Снижает нагрузку на CPU ещё сильнее

### Проверка и включение

```bash
# Узнать имя интерфейса:
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')
echo "Интерфейс: $IFACE"

# Посмотреть текущее состояние offloads:
ethtool -k $IFACE | grep -E 'udp|gso|gro|tso|tx-checksum'

# Что ищем:
# generic-segmentation-offload: on          ← GSO (software) — нужно ON
# generic-receive-offload: on               ← GRO (receive) — нужно ON
# tx-checksumming: on                       ← нужно ON (GSO depends on it)
# tx-udp-segmentation: on [или off fixed]   ← если 'fixed' — нельзя изменить

# Включить tx-udp-segmentation если не 'fixed':
ethtool -K $IFACE tx-udp-segmentation on 2>&1
# → Если "Could not change any device features" или "[fixed]" — не поддерживается NIC/hypervisor

# Включить rx-udp-gro-forwarding (ядро 6.2+):
ethtool -K $IFACE rx-udp-gro-forwarding on 2>&1
```

**Проверка поддержки на вашем VPS:**

```bash
ethtool -k $IFACE | grep -E 'tx-udp-segmentation|generic-segmentation'
```

| Результат | Что делать |
|---|---|
| `tx-udp-segmentation: on` | Уже включено, хорошо |
| `tx-udp-segmentation: off` | Включить командой выше |
| `tx-udp-segmentation: off [fixed]` | Не поддерживается hypervisor — пропустить |
| Строка отсутствует | Ядро < 4.18 или старый драйвер |

**Важно:** если `tx-udp-segmentation` недоступно — это **не катастрофа**. quic-go всё равно использует software GSO (`UDP_SEGMENT` socket option), что само по себе даёт большой прирост. Hardware USO — дополнительный бонус.

### Как отключить quic-go GSO (если нужно для диагностики)

```yaml
# В docker-compose.yml добавить environment:
environment:
  - QUIC_GO_DISABLE_GSO=false   # false = включён (по умолчанию)
  # QUIC_GO_DISABLE_GSO=true   # только для отладки!
```

Не выставляйте `QUIC_GO_DISABLE_GSO=true` в production — это отключит software GSO.

### Персистентность ethtool (через systemd)

```bash
cat > /etc/systemd/system/nic-offloads.service << EOF
[Unit]
Description=NIC offload tuning
After=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '\
  IFACE=\$(ip route get 1.1.1.1 | grep -oP "dev \K\S+"); \
  ethtool -K \$IFACE gso on gro on tso on tx-checksumming on 2>/dev/null; \
  ethtool -K \$IFACE tx-udp-segmentation on 2>/dev/null || true; \
  ethtool -K \$IFACE rx-udp-gro-forwarding on 2>/dev/null || true; \
  ethtool -G \$IFACE rx 4096 tx 4096 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nic-offloads
systemctl start nic-offloads
```

---

## 3. NIC ring buffer — дропы при burst

### Проблема

На VPS кольцевые буферы NIC (ring buffers) по умолчанию часто установлены в **256 пакетов** при аппаратном максимуме **4096**. При burst Hysteria2 UDP или XHTTP packet-up — пакеты дропаются ещё до того как ядро успевает их обработать.

### Диагностика

```bash
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')
ethtool -g $IFACE
# → Pre-set maximums: RX: 4096, TX: 4096
# → Current hardware settings: RX: 256, TX: 256   ← часто именно так

# Мониторинг дропов (смотреть до и после нагрузки):
ip -s link show $IFACE | grep -A4 'RX\|TX'
ethtool -S $IFACE | grep -iE 'drop|miss|fifo|err' | grep -v ': 0'
```

### Решение

```bash
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')

# Узнать максимум:
MAX_RX=$(ethtool -g $IFACE 2>/dev/null | awk '/Pre-set max/{found=1} found && /RX:/{print $2; exit}')
MAX_TX=$(ethtool -g $IFACE 2>/dev/null | awk '/Pre-set max/{found=1} found && /TX:/{print $2; exit}')
echo "Max RX=$MAX_RX TX=$MAX_TX"

# Установить максимум (или 4096 если max > 4096):
RX=$(( MAX_RX > 4096 ? 4096 : MAX_RX ))
TX=$(( MAX_TX > 4096 ? 4096 : MAX_TX ))
ethtool -G $IFACE rx $RX tx $TX 2>/dev/null || echo "Ring buffer не поддерживается"
```

> **Примечание:** Большие ring buffers немного увеличивают latency на ненагруженном сервере (буферизация). Для VPN-сервера с burst-трафиком (Hysteria2 UDP) это приемлемый компромисс.

---

## 4. Дополнения к sysctl

### Что нужно добавить в `/etc/sysctl.d/99-server.conf`

```bash
# ── TCP: отключить кеширование метрик ────────────────────────────────────────
# VPN: каждое подключение — новый клиент с разным RTT.
# Кешированные "tcp metrics" от старых сессий портят slow-start нового клиента.
net.ipv4.tcp_no_metrics_save=1

# ── TCP: не замедляться после idle ───────────────────────────────────────────
# VLESS TCP: если передача данных прерывается (пользователь смотрит, не качает),
# BBR сбрасывает cwnd обратно к IW10. tcp_slow_start_after_idle=0 отключает это.
net.ipv4.tcp_slow_start_after_idle=0

# ── NAPI: больше пакетов за poll-цикл ────────────────────────────────────────
# Дефолт 300. Для 10Gbps канала и Hysteria2 UDP burst поднять до 600-1000.
# Следите за /proc/net/softnet_stat (3-й столбец) — если растёт, нужно больше.
net.core.netdev_budget=600
net.core.netdev_budget_usecs=8000

# ── Ancillary socket буфер (нужен для UDP GSO/GRO) ───────────────────────────
# sendmsg с GSO передаёт control messages (cmsg) с UDP_SEGMENT.
# Дефолт 10240 байт — может быть мало при высоком GSO batch size.
net.core.optmem_max=65536

# ── UDP буферы (явные минимумы) ───────────────────────────────────────────────
# Гарантированный минимум для UDP сокетов. quic-go пытается увеличить буферы
# до rmem_max/wmem_max — убедиться что минимум не занижает.
net.ipv4.udp_rmem_min=8192
net.ipv4.udp_wmem_min=8192

# ── TCP moderate rcvbuf (авто-тюнинг) ────────────────────────────────────────
# Разрешает ядру авто-уменьшать recv buffer когда память под давлением.
# Полезно при 2 GB RAM и 500+ одновременных соединений.
net.ipv4.tcp_moderate_rcvbuf=1
```

### Что исправить в существующей конфигурации

```bash
# БЫЛО (из гайда):
net.core.netdev_max_backlog=16384

# ЛУЧШЕ для 10 Gbps:
net.core.netdev_max_backlog=32768
# При 10 Gbps и Hysteria2 UDP burst 16384 может переполниться.
# Проверяйте: watch -n1 'cat /proc/net/softnet_stat | awk "{sum+=\$2} END {print sum}"'
# Если 2-й столбец растёт под нагрузкой — увеличьте backlog.
```

### Итоговый дополненный блок

```bash
cat >> /etc/sysctl.d/99-server.conf << 'EOF'

# ── Дополнения (результат исследования 2026) ─────────────────────────────────
net.ipv4.tcp_no_metrics_save=1
net.ipv4.tcp_slow_start_after_idle=0
net.core.netdev_budget=600
net.core.netdev_budget_usecs=8000
net.core.optmem_max=65536
net.ipv4.udp_rmem_min=8192
net.ipv4.udp_wmim_min=8192
net.ipv4.tcp_moderate_rcvbuf=1
EOF

# Исправить netdev_max_backlog:
sed -i 's/netdev_max_backlog=16384/netdev_max_backlog=32768/' \
  /etc/sysctl.d/99-server.conf

sysctl --system
```

---

## 5. CPU governor

### Проблема

Linux по умолчанию использует governor `powersave` или `schedutil`. На VPS это зависит от гипервизора. Если сервер находится на governor `powersave` — CPU работает на минимальной частоте и поднимается только при нагрузке. Для криптографических операций (TLS) задержка масштабирования частоты добавляет latency.

**Задокументированный эффект:** на 100G хостах смена `powersave` → `performance` давала +30% throughput.

### Проверка и настройка

```bash
# Проверить текущий governor:
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null \
  || echo "cpufreq не доступен (типично для VPS)"

# Если cpufreq доступен — установить performance:
apt install -y linux-tools-generic 2>/dev/null \
  || apt install -y linux-tools-$(uname -r) 2>/dev/null || true

cpupower frequency-set -g performance 2>/dev/null \
  || echo "Не поддерживается этим VPS провайдером"

# Проверить результат:
cpupower frequency-info 2>/dev/null | grep governor
```

> **Реальность VPS:** На KVM VPS governor часто недоступен вообще (`cpufreq not found`) — CPU управляется гипервизором. Это нормально — просто пропустите шаг. На дедиках (bare metal) это обязательно.

---

## 6. Nginx: worker_processes

### Проблема

В гайде прописано:
```nginx
worker_processes 1;   # "1 CPU → 1 воркер"
```

На 2-ядерном сервере это **неоптимально**. Nginx stream (пассивный TCP passthrough) — event-driven, но всё равно выиграет от 2 воркеров, особенно при большом количестве одновременных соединений XHTTP.

### Исправление

```nginx
worker_processes auto;   # автоматически = количество CPU cores = 2
# или явно:
worker_processes 2;
```

**Примечание:** Для VLESS TCP REALITY nginx — только passthrough (ssl_preread). Для XHTTP — nginx тоже passthrough через stream. HTTP-воркеры (блок http {}) обрабатывают только :80 и фейк-сайты (:8080, :8443). Так что влияние умеренное, но есть.

---

## 7. Анализ QUIC window sizes

### Текущий выбор в гайде

```json
"initStreamReceiveWindow": 6291456,    // 6 MB
"maxStreamReceiveWindow": 6291456,     // 6 MB (maxStream = initStream)
"initConnectionReceiveWindow": 15728640, // 15 MB
"maxConnectionReceiveWindow": 15728640   // 15 MB
```

Обоснование: Chrome использует 6/15 MB → fingerprint совпадает с браузером.

### Что рекомендует официальный hysteria2

```yaml
# Из официальной документации hysteria2:
quic:
  initStreamReceiveWindow: 26843545    # 25.6 MB
  maxStreamReceiveWindow: 26843545
  initConnectionReceiveWindow: 67108864  # 64 MB
  maxConnectionReceiveWindow: 67108864
```

### Максимальный throughput = f(RTT, window)

```
MaxThroughput = Window / RTT

RTT 50ms, window 6MB:   6*8 / 0.05 = 960 Mbit/s ← потолок при 6MB
RTT 50ms, window 25MB: 25*8 / 0.05 = 4000 Mbit/s
RTT 100ms, window 6MB:  6*8 / 0.10 = 480 Mbit/s ← у пользователей в СНГ бывает >100ms
```

### Рекомендация

Для чистого **Xray↔Xray** трафика (Hysteria2) fingerprinting не критичен — клиент всё равно идентифицируется через пароль. Можно поднять windows:

```json
// Компромисс: больше throughput, меньше похоже на Chrome, но всё ещё не hysteria2 default:
"initStreamReceiveWindow": 8388608,    // 8 MB
"maxStreamReceiveWindow": 8388608,
"initConnectionReceiveWindow": 20971520, // 20 MB
"maxConnectionReceiveWindow": 20971520
```

Или полный hysteria2 default (если DPI не анализирует QUIC flow control параметры):
```json
"initStreamReceiveWindow": 26843545,
"maxStreamReceiveWindow": 26843545,
"initConnectionReceiveWindow": 67108864,
"maxConnectionReceiveWindow": 67108864
```

> **Итог:** Если RTT до клиента > 50ms (типично для РФ ↔ EU/US серверов) — окно 6/15 MB **ограничивает скорость**. При RTT 100ms и окне 6MB потолок ~480 Mbit/s. Поднять до 20/8 MB — разумный компромисс.

---

## 8. Что в гайде сделано правильно (подтверждено)

### 8.1. fq лучше fq_codel для сервера

**Подтверждено.** fq_codel разработан для роутеров/forwarding-устройств. На end-системе CoDel начинает дропать пакеты на отправляющей стороне вместо использования backpressure. Это увеличивает latency и снижает throughput через TCP-ретрансмиты. Для BBR+fq пара оптимальна.

### 8.2. quic-go GSO работает автоматически

**Подтверждено.** quic-go (используется в Xray для Hysteria2) автоматически включает UDP GSO на Linux ≥ 4.18. Никаких дополнительных настроек Xray не требует. Нужно только убедиться что `QUIC_GO_DISABLE_GSO` не установлен в `true`.

### 8.3. 16 MB буферы достаточны

**Подтверждено.** Официальная документация quic-go рекомендует **минимум** `net.core.rmem_max=7340032` (7 MB). 16 MB в гайде — хороший запас. Поднимать выше при 2 GB RAM нет смысла.

### 8.4. tcp_tw_reuse=1 для XHTTP packet-up

**Правильно и необходимо.** В режиме packet-up каждый POST = новое TCP-соединение. Без tw_reuse при тысячах запросов в минуту TIME_WAIT сокеты заполнят таблицу. `tcp_tw_reuse=1` позволяет переиспользовать сокеты для исходящих соединений.

### 8.5. ECDSA P-256 TLS

**Правильно.** P-256 handshake в 5-10 раз быстрее RSA-4096. Для XHTTP packet-up (каждый POST = новый TLS handshake до CDN) это критично.

### 8.6. selfsteal для REALITY

**Правильно.** Target на `127.0.0.1:8443` (Nginx с реальным сайтом) — лучшая маскировка. При active probe ТСПУ получает настоящий TLS сертификат + HTML вместо RST.

---

## 9. Что сделано неправильно / спорно

### 9.1. sniffing включён для QUIC без необходимости

В конфиге VLESS TCP REALITY:
```json
"sniffing": {
    "enabled": true,
    "destOverride": ["http", "tls", "quic"]
}
```

`quic` в `destOverride` заставляет Xray парсить QUIC Initial пакеты для определения SNI. На порту 20001 (VLESS TCP, только TCP) это **бессмысленно** — QUIC пакеты туда не придут. Это лишний overhead при каждом пакете.

**Исправление:**
```json
"sniffing": {
    "enabled": true,
    "destOverride": ["http", "tls"]
}
```

### 9.2. ip_local_port_range vs Hysteria2 port hopping

В гайде: `net.ipv4.ip_local_port_range=10000 65535`

И одновременно: iptables PREROUTING перенаправляет UDP 20000-50000 → 443.

Диапазон 20000-50000 пересекается с `ip_local_port_range`. Это означает, что ядро может попытаться использовать порт из 20000-50000 как исходящий ephemeral порт для локальных соединений → эти соединения попадут под PREROUTING REDIRECT → бесконечный цикл.

Для UDP это менее вероятно (REDIRECT работает только на входящие), но для TCP-соединений Xray, выходящих через эти порты — возможны коллизии.

**Рекомендация:**
```bash
# Уменьшить ephemeral range, чтобы не пересекался с port hopping:
net.ipv4.ip_local_port_range=50001 65535
# ИЛИ использовать port hopping вне ephemeral range:
# iptables для UDP 10000-19999 → 443
# и оставить ip_local_port_range=10000 65535 как в гайде
```

### 9.3. tcp_mtu_probing=1 и VPS

`net.ipv4.tcp_mtu_probing=1` включает Black Hole detection + MTU probing. На большинстве VPS MTU фиксирован (1500 или 1450). Активный MTU probing добавляет задержку при каждом новом соединении.

Рекомендация: `net.ipv4.tcp_mtu_probing=0` (не трогать, пусть работает по умолчанию без проб). Если нужна PMTUD — она работает и без этого параметра через стандартные ICMP Fragmentation Needed.

---

## 10. Итоговый приоритизированный план

### Приоритет 1 — Высокий эффект, безопасно

```bash
# 1. Добавить sysctl
cat >> /etc/sysctl.d/99-server.conf << 'EOF'
net.ipv4.tcp_no_metrics_save=1
net.ipv4.tcp_slow_start_after_idle=0
net.core.netdev_budget=600
net.core.netdev_budget_usecs=8000
net.core.optmem_max=65536
net.ipv4.udp_rmem_min=8192
net.ipv4.udp_wmem_min=8192
net.ipv4.tcp_moderate_rcvbuf=1
EOF
sed -i 's/netdev_max_backlog=16384/netdev_max_backlog=32768/' /etc/sysctl.d/99-server.conf
sysctl --system

# 2. NIC ring buffers
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')
ethtool -g $IFACE
# Установить максимум (заменить 4096 если у вас другой макс):
ethtool -G $IFACE rx 4096 tx 4096 2>/dev/null || true

# 3. UDP offloads
ethtool -K $IFACE tx-udp-segmentation on 2>/dev/null || true
ethtool -K $IFACE rx-udp-gro-forwarding on 2>/dev/null || true

# 4. nginx worker_processes → auto
sed -i 's/worker_processes 1;/worker_processes auto;/' /etc/nginx/nginx.conf
nginx -t && systemctl reload nginx
```

### Приоритет 2 — Потенциально высокий эффект, требует тестирования

```bash
# 5. QUIC windows — поднять если RTT > 50ms к клиентам
# В конфиге Xray Hysteria2, блок finalmask.quicParams:
# "initStreamReceiveWindow": 8388608,
# "maxStreamReceiveWindow": 8388608,
# "initConnectionReceiveWindow": 20971520,
# "maxConnectionReceiveWindow": 20971520,

# 6. CPU governor (если доступен):
cpupower frequency-set -g performance 2>/dev/null || true
```

### Приоритет 3 — Требует перезагрузки

```bash
# 7. XanMod kernel с BBR3 — только если нужна максимальная производительность
# и VPS позволяет смену ядра:
# wget -qO - https://dl.xanmod.org/archive.key | \
#   gpg --dearmor -o /usr/share/keyrings/xanmod-archive-keyring.gpg
# ... (см. секцию 1 выше)
```

### Исправления конфига Xray (без риска)

```json
// VLESS TCP REALITY inbound — убрать "quic" из destOverride:
"sniffing": {
    "enabled": true,
    "destOverride": ["http", "tls"]
}
```

---

## 11. Диагностика после применения

### Проверка tx-udp-segmentation

```bash
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')
ethtool -k $IFACE | grep -E 'tx-udp-segment|generic-seg|generic-rec'
```

### Мониторинг NAPI backlog (overflow → нужно больше netdev_budget)

```bash
# Второй столбец — dropped frames в backlog
# Третий — ksoftirqd ran out of budget
watch -n 2 'awk "{printf \"dropped=%s budget_squeeze=%s\n\", \$2, \$3}" /proc/net/softnet_stat | head -4'
```

### Мониторинг ring buffer drops

```bash
IFACE=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')
watch -n 2 "ethtool -S $IFACE | grep -iE 'drop|miss|fifo' | grep -v ': 0'"
```

### Проверка UDP buffers (quic-go logging)

```bash
# quic-go выдаёт предупреждение если буферы меньше рекомендуемых:
docker logs remnawave-node 2>&1 | grep -i 'udp\|buffer\|gso'
# Если видите "failed to sufficiently increase buffer" — буферы всё ещё малы
```

### Быстрый тест скорости Hysteria2 upload (с сервера клиенту)

```bash
# На клиенте (через VPN):
iperf3 -c SERVER_IP -p 5201 -u -b 500M -t 10 -R
# -R: reverse = сервер → клиент (download клиента = upload сервера)
```

---

## 12. Справочник: что quic-go делает автоматически

Xray использует quic-go под капотом. Документально подтверждённые автооптимизации:

| Оптимизация | Автоматически | Условие |
|---|---|---|
| UDP GSO (sendmsg batching) | ✅ | Linux ≥ 4.18 |
| UDP GRO (recvmmsg batching) | ✅ | Linux ≥ 5.0 |
| DPLPMTUD (Path MTU discovery) | ✅ | По умолчанию |
| Увеличение socket buffers | ✅ | До rmem_max/wmem_max |
| Buffer size warning log | ✅ | Если буфер < 7MB |

> Вывод: чтобы quic-go работал оптимально, нужно только правильно выставить `net.core.rmem_max` и `net.core.wmem_max` (≥ 7MB, в гайде уже 16MB ✅), и убедиться что `QUIC_GO_DISABLE_GSO` не установлен.

---

*Исследование: Xray-core v26.6.1, quic-go docs (2025), Tailscale UDP throughput blog, IETF BBRv3 draft-04, Red Hat RHEL 9/10 performance guides, LinuxCapable XanMod guides, GitHub XTLS/Xray-core issues — июнь 2026*
