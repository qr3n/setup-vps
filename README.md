# VPS Setup CLI

Автоматизированный скрипт для настройки стелс-VPN сервера на базе **Xray-core v26.6.1**.

### Возможности
- **VLESS TCP REALITY + Vision** (Self-steal на ваш сайт)
- **VLESS XHTTP** (Обход через CDN)
- **Hysteria2** (Высокая скорость на UDP с port hopping)
- **Безопасность:** SSH hardening, Port Knocking, UFW, Fail2ban
- **Оптимизация:** BBR + fq, тюнинг TCP буферов

### Установка (One-liner)

Выполните команду на чистом сервере (Ubuntu 22.04+ / Debian 12+):

```bash
curl -sL https://raw.githubusercontent.com/YOUR_USERNAME/setup-vps/main/install.sh | sudo bash -s -- --run
```

*Не забудьте заменить `YOUR_USERNAME` на ваш ник на GitHub.*

### Ручная установка
Если вы уже клонировали репозиторий:
1. `python3 -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install -e .`
4. `setup-vps`

### Требования
- Чистый сервер (рекомендуется)
- Домен (для SSL сертификатов и маскировки)
- Доступ по SSH с правами root

---
*Основано на итоговом гайде по настройке Xray 2026 года.*
