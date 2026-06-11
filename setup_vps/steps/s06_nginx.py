# setup_vps/steps/s06_nginx.py
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

NGINX_CONF = "/etc/nginx/nginx.conf"
NGINX_VPN_CONF = "/etc/nginx/sites-available/vpn"
NGINX_MARKER = "# setup-vps: nginx"


def _nginx_conf_content(main_domain: str, cdn_domain: str) -> str:
    # We need to ensure stream module is loaded and configured correctly
    # Usually in Ubuntu it's already there, but we might need to add the stream block
    return f"""\
user www-data;
worker_processes auto;
pid /run/nginx.pid;
include /etc/nginx/modules-enabled/*.conf;

events {{
    worker_connections 10000;
}}

stream {{
    map $ssl_preread_server_name $backend {{
        {main_domain}   xray_reality;
        {cdn_domain}    xray_xhttp;
        default         nginx_https;
    }}

    upstream xray_reality {{ server 127.0.0.1:1443; }}
    upstream xray_xhttp   {{ server 127.0.0.1:8001; }}
    upstream nginx_https  {{ server 127.0.0.1:8443; }}

    server {{
        listen 443 reuseport;
        listen [::]:443 reuseport;
        ssl_preread on;
        proxy_pass $backend;
        proxy_timeout 3600s;
    }}
}}

http {{
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    server_tokens off;

    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    gzip on;

    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}}
"""


def _vpn_site_content(main_domain: str, cdn_domain: str) -> str:
    return f"""\
# setup-vps: vpn sites
server {{
    listen 80;
    listen [::]:80;
    server_name {main_domain} {cdn_domain};
    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 127.0.0.1:8443 ssl;
    http2 on;
    server_name {main_domain} {cdn_domain};

    ssl_certificate     /etc/letsencrypt/live/{main_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{main_domain}/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305;
    
    root /var/www/html;
    index index.html;

    location / {{
        try_files $uri $uri/ =404;
    }}
}}
"""


class NginxStep(BaseStep):
    name = "s06_nginx"
    title = "Nginx"
    description = "nginx stream (SNI routing) + HTTP vhosts"

    def preflight(self, config, state) -> bool:
        if not Path(NGINX_VPN_CONF).exists():
            return False
        content = Path(NGINX_CONF).read_text()
        if "stream {" not in content:
            return False
        active = run_shell("systemctl is-active nginx", capture=True).stdout.strip()
        return active == "active"

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        from setup_vps.ui import run_with_live_logs

        def install_nginx(on_output):
            cmds = [
                "curl -sS https://nginx.org/keys/nginx_signing.key | gpg --dearmor | dd of=/usr/share/keyrings/nginx-archive-keyring.gpg 2>/dev/null",
                "ID=$(lsb_release -is | tr '[:upper:]' '[:lower:]')",
                "CODENAME=$(lsb_release -cs)",
                "echo \"deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/mainline/$ID $CODENAME nginx\" > /etc/apt/sources.list.d/nginx.list",
                "echo -e \"Package: *\\nPin: origin nginx.org\\nPin: release o=nginx\\nPin-Priority: 900\\n\" > /etc/apt/preferences.d/99nginx",
                "apt-get update -qq",
                "apt-get install -yq nginx"
            ]
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            return run_shell(" && ".join(cmds), log_path=log, on_output=on_output, env=env)

        print_info("Ensuring latest mainline Nginx is installed...")
        r = run_with_live_logs("Installing Nginx", install_nginx)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx installation failed")

        print_info("Writing main nginx.conf...")
        Path(NGINX_CONF).write_text(_nginx_conf_content(config.main_domain, config.cdn_domain))

        print_info("Writing vpn site config...")
        Path(NGINX_VPN_CONF).write_text(_vpn_site_content(config.main_domain, config.cdn_domain))
        
        enabled_path = Path("/etc/nginx/sites-enabled/vpn")
        if not enabled_path.exists():
            enabled_path.symlink_to(NGINX_VPN_CONF)

        # Remove default
        default_path = Path("/etc/nginx/sites-enabled/default")
        if default_path.exists():
            default_path.unlink()

        print_info("Testing nginx config...")
        r = run_shell("nginx -t", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx -t failed")

        print_info("Restarting nginx...")
        r = run_shell("systemctl restart nginx", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx restart failed")

        return StepResult(success=True, message="Nginx configured")

    def verify(self, config, state) -> VerifyResult:
        checks = {}

        test = run_shell("nginx -t 2>&1", capture=True)
        checks["nginx_t"] = "ok" if test.returncode == 0 else test.stdout.strip()[:100]
        test_ok = test.returncode == 0

        active = run_shell("systemctl is-active nginx", capture=True).stdout.strip()
        checks["nginx_active"] = active
        active_ok = active == "active"

        ports = run_shell("ss -tlnp", capture=True).stdout
        checks["port_80"] = "listening" if ":80 " in ports else "MISSING"
        checks["port_443"] = "listening" if ":443 " in ports else "MISSING"
        checks["port_8443"] = "listening" if "127.0.0.1:8443 " in ports else "MISSING"

        passed = test_ok and active_ok and ":443 " in ports
        return VerifyResult(passed=passed, checks=checks)
        return StepResult(success=False, error=r.stderr, message="nginx -t failed")

        print_info("Restarting nginx...")
        r = run_shell("systemctl restart nginx", log_path=log)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx restart failed")

        return StepResult(success=True, message="Nginx configured")

