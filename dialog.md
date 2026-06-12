Собрал всё из официальной доки Hysteria2, quic-go, форумов и статей 2026. Разбиваю по слоям.

---

## Предварительно — проверить среду

```bash
# Тип виртуализации (OpenVZ не поддерживает BBR)
apt install virt-what -y && virt-what

# Версия ядра (нужна 5.18+ для BBRv3 native)
uname -r

# Текущий congestion control
sysctl net.ipv4.tcp_congestion_control
```

---

## 1. BBR — congestion control для TCP / XHTTP

BBRv3 нативно включён в mainline Linux 6.x. В 2026 году его стабильность и производительность в условиях потерь пакетов на международных каналах с RTT >150ms значительно превышает старые CUBIC и community-форки.

```bash
# Загрузить модуль (если ядро < 6.x)
modprobe tcp_bbr
echo "tcp_bbr" >> /etc/modules-load.d/bbr.conf

# Применить
cat > /etc/sysctl.d/10-bbr.conf << 'EOF'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
EOF

sysctl -p /etc/sysctl.d/10-bbr.conf

# Проверка
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc
# Должно вернуть: bbr и fq
```

> `fq` (Fair Queue) — обязательный пейринг с BBR. Оба (`fq` и `fq_codel`) поддерживают pacing, но `fq` рекомендуется командой Google для BBR.

---

## 2. Ядро sysctl — сетевые буферы и TCP стек

У тебя 10 Gbps канал + 2 GB RAM. Буферы подобраны так, чтобы не съедать память при пиковой нагрузке.

```bash
cat > /etc/sysctl.d/20-network-performance.conf << 'EOF'
# ─── TCP буферы (16 MB max) ───────────────────────────────────
# BDP при 10Gbps / RTT 50ms ≈ 62MB, но 16MB — разумный потолок для 2GB RAM
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 524288 16777216

# ─── UDP буферы (критично для Hysteria2 / QUIC) ────────────────
# Официальная рекомендация Hysteria2 и quic-go: min 7–16 MB
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216

# ─── Входящая очередь пакетов ─────────────────────────────────
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 8192
net.ipv4.tcp_max_syn_backlog = 8192

# ─── BBR / Fast Open / MTU probing ────────────────────────────
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mtu_probing = 1

# ─── TIME_WAIT / connection recycling ─────────────────────────
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_max_tw_buckets = 1440000
net.ipv4.ip_local_port_range = 1024 65535

# ─── Память под сокеты ────────────────────────────────────────
net.ipv4.tcp_mem = 786432 1048576 1572864

# ─── VM: не свопить без острой нужды ──────────────────────────
vm.swappiness = 10
EOF

sysctl -p /etc/sysctl.d/20-network-performance.conf
```

---

## 3. Hysteria2 — QUIC flow control windows

Официальная документация Hysteria2 даёт увеличенные значения receive windows для высокопропускных сценариев. Рекомендуемое соотношение stream:connection = 2:5.

В конфиг сервера (`config.yaml`):

```yaml
quic:
  initStreamReceiveWindow: 26843545    # 25.6 MB
  maxStreamReceiveWindow: 26843545
  initConnReceiveWindow: 67108864      # 64 MB
  maxConnReceiveWindow: 67108864
  maxIdleTimeout: 30s
  maxIncomingStreams: 1024
  disablePathMTUDiscovery: false       # DPLPMTUD включён — находит оптимальный MTU
```

**Congestion control** в конфиге Hysteria2 — используй `bbr`, не `brutal`:
```yaml
bandwidth:
  # НЕ указывай bandwidth если используешь BBR — он сам определяет
congestion:
  type: bbr    # brutal требует точно знать скорость канала
```

---

## 4. GSO — Generic Segmentation Offload для QUIC

quic-go (движок Hysteria2) использует GSO (Generic Segmentation Offload) начиная с Linux kernel 4.18. Это позволяет передавать до 64 KB буфером в ядро вместо посылки каждого UDP-пакета отдельным syscall — критически важно для throughput. GSO включается автоматически, но проверить можно:

```bash
# Проверить GSO на сетевом интерфейсе
ethtool -k eth0 | grep segmentation

# Если вдруг нужно включить:
ethtool -K eth0 gso on gro on tso on
```

---

## 5. IRQ affinity и NIC queue — для 2 ядер

```bash
# Посмотреть очереди NIC
ethtool -l eth0

# Назначить 2 очереди = 2 ядра (если NIC поддерживает)
ethtool -L eth0 combined 2

# irqbalance — автобалансировка прерываний по двум ядрам
apt install irqbalance -y
systemctl enable --now irqbalance

# Проверить как распределены прерывания
cat /proc/interrupts | grep eth0
```

---

## 6. txqueuelen — очередь передачи NIC

```bash
ip link set eth0 txqueuelen 10000

# Сохранить после перезагрузки
cat > /etc/networkd-dispatcher/configured.d/txqueue << 'EOF'
#!/bin/bash
ip link set eth0 txqueuelen 10000
EOF
chmod +x /etc/networkd-dispatcher/configured.d/txqueue
```

---

## 7. Приоритет процесса Hysteria2

На устройствах с ограниченными CPU-ресурсами высокая нагрузка вызывает latency jitter. Это можно смягчить, повысив приоритет процесса.

```bash
mkdir -p /etc/systemd/system/hysteria-server.service.d/

cat > /etc/systemd/system/hysteria-server.service.d/priority.conf << 'EOF'
[Service]
Nice=-5
EOF

systemctl daemon-reload && systemctl restart hysteria-server
```

---

## 8. Проверка итогового состояния

```bash
# BBR активен?
sysctl net.ipv4.tcp_congestion_control
# → bbr

# UDP буферы
sysctl net.core.rmem_max net.core.wmem_max
# → 16777216

# GSO
ethtool -k eth0 | grep generic-segmentation

# Текущий throughput
iperf3 -s &  # на сервере
# iperf3 -c <server_ip> -t 30  # с клиента

# Загрузка CPU Hysteria2 при нагрузке
top -p $(pidof hysteria)
```

---

## Итог по протоколам

| Протокол | Congestion Control | Ключевой тюнинг |
|---|---|---|
| TCP / XHTTP | BBR + fq | tcp_rmem/wmem, Fast Open, MTU probing |
| Hysteria2 | BBR (встроенный) | UDP rmem_max=16MB, QUIC windows, GSO, Nice=-5 |

> **Важно про 2 GB RAM:** не увеличивай `rmem_max`/`wmem_max` выше 16–32 MB. При множестве одновременных соединений это может привести к OOM. Мониторь `free -m` под нагрузкой.
> 
> Всё хорошо читается. Коротко по каждому пункту:

**NIC queues:** уже на максимуме — `Combined: 2/2`. Оба ядра задействованы, `ethtool -L` не нужен.

**GSO:** `generic-segmentation-offload: on` — ок для TCP/XHTTP.

**Проблема:** `tx-udp-segmentation: off` — это важно для Hysteria2. quic-go использует именно UDP GSO (через `UDP_SEGMENT` socket option) для батчинга пакетов. Оно не отмечено как `[fixed]`, значит можно включить:

```bash
ethtool -K eth0 tx-udp-segmentation on
```

Проверь:
```bash
ethtool -k eth0 | grep tx-udp-segmentation
# Должно быть: tx-udp-segmentation: on
```

Чтобы не слетало после перезагрузки, добавь в `/etc/networkd-dispatcher/configured.d/txqueue` (файл ты уже создавал):

```bash
cat > /etc/networkd-dispatcher/configured.d/txqueue << 'EOF'
#!/bin/bash
ip link set eth0 txqueuelen 10000
ethtool -K eth0 tx-udp-segmentation on
EOF
chmod +x /etc/networkd-dispatcher/configured.d/txqueue
```

Если `ethtool -K` вернёт ошибку — значит виртуальный драйвер не поддерживает (тогда `[fixed]` просто не отобразился), и Hysteria2 автоматически откатится на non-GSO путь без каких-либо последствий.