# setup_vps/steps/s06_nginx.py
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info

NGINX_CONF = "/etc/nginx/nginx.conf"
NGINX_VPN_CONF = "/etc/nginx/sites-available/vpn"
NGINX_MARKER = "# setup-vps: nginx"

# Port layout:
#   443         – stream layer, ssl_preread SNI dispatch (plain TCP, no TLS here)
#   8443        – nginx HTTP backend: main_domain + cdn_domain (TLS terminated here)
#   8444        – nginx HTTP backend: node_domain (TLS terminated here, grpc_pass to 2222)
#   1443        – Xray REALITY
#   8001        – Xray XHTTP / CDN


def _nginx_conf_content(main_domain: str, cdn_domain: str, node_domain: str) -> str:
    return f"""\
user www-data;
worker_processes auto;
pid /run/nginx.pid;

include /etc/nginx/modules-enabled/*.conf;

events {{
    worker_connections 10000;
}}

# ── Stream: SNI-based dispatch on port 443 ───────────────────────────────────
# ssl_preread reads the ClientHello WITHOUT terminating TLS.
# Each upstream receives the raw TLS stream; TLS is terminated inside the
stream {{
    map $ssl_preread_server_name $backend {{
        {main_domain}   xray_reality;
        {cdn_domain}    xray_xhttp;
        {node_domain}   nginx_node_grpc;
        default         nginx_main;
    }}

    upstream xray_reality    {{ server 127.0.0.1:1443; }}
    upstream xray_xhttp      {{ server 127.0.0.1:8001; }}
    upstream nginx_main      {{ server 127.0.0.1:8443; }}
    upstream nginx_node_grpc {{ server 127.0.0.1:8444; }}

    server {{
        listen 443 reuseport;
        listen [::]:443 reuseport;
        ssl_preread on;
        proxy_pass $backend;
        proxy_timeout 3600s;
        proxy_connect_timeout 10s;
    }}
}}

# ── HTTP: TLS termination + vhosts ───────────────────────────────────────────
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


def _vpn_site_content(main_domain: str, cdn_domain: str, node_domain: str) -> str:
    # Common SSL settings snippet to avoid repetition
    ssl_main = f"""\
    ssl_certificate     /etc/letsencrypt/live/{main_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{main_domain}/privkey.pem;"""

    ssl_node = f"""\
    ssl_certificate     /etc/letsencrypt/live/{node_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{node_domain}/privkey.pem;"""

    ssl_common = """\
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;"""

    return f"""\
# setup-vps: vpn sites

# ── HTTP → HTTPS redirect + ACME challenges ──────────────────────────────────
server {{
    listen 80;
    listen [::]:80;
    server_name {main_domain} {cdn_domain} {node_domain};

    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

# ── Backend: main + cdn domains (port 8443) ──────────────────────────────────
# Receives raw TLS from the stream proxy; terminates it here.
server {{
    listen 127.0.0.1:8443 ssl;
    http2 on;
    server_name {main_domain} {cdn_domain};

{ssl_main}
{ssl_common}

    root /var/www/html;
    index index.html;

    location / {{
        try_files $uri $uri/ =404;
    }}
}}

# ── Backend: node_domain gRPC proxy (port 8444) ──────────────────────────────
# Separate port so it doesn't conflict with 8443 above.
# Terminates TLS for node_domain, then proxies plain gRPC to remnanode:2222.
server {{
    listen 127.0.0.1:8444 ssl;
    http2 on;
    server_name {node_domain};

{ssl_node}
{ssl_common}

    # gRPC upstream: remnawave node listens on plain gRPC (no TLS), port 2222
    location / {{
        grpc_pass grpc://127.0.0.1:2222;
        grpc_set_header Host $host;
        grpc_set_header X-Real-IP $remote_addr;
        grpc_read_timeout 300s;
        grpc_send_timeout 300s;

        # Proper gRPC error page instead of nginx 502 HTML
        error_page 502 = /grpc_error_502;
    }}

    location = /grpc_error_502 {{
        internal;
        default_type application/grpc;
        add_header grpc-status 14;
        add_header grpc-message "remnanode unavailable";
        add_header content-length 0;
        return 204;
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
        # Check that the new architecture (8444) is already deployed
        if "8444" not in content and "8444" not in Path(NGINX_VPN_CONF).read_text():
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
                'echo "deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/mainline/$ID $CODENAME nginx" > /etc/apt/sources.list.d/nginx.list',
                'echo -e "Package: *\\nPin: origin nginx.org\\nPin: release o=nginx\\nPin-Priority: 900\\n" > /etc/apt/preferences.d/99nginx',
                "apt-get update -qq",
                "apt-get install -yq nginx",
            ]
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            return run_shell(" && ".join(cmds), log_path=log, on_output=on_output, env=env)

        print_info("Ensuring latest mainline Nginx is installed...")
        r = run_with_live_logs("Installing Nginx", install_nginx)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="nginx installation failed")

        print_info("Writing main nginx.conf...")
        Path(NGINX_CONF).write_text(
            _nginx_conf_content(config.main_domain, config.cdn_domain, config.node_domain)
        )

        print_info("Writing vpn site config...")
        Path("/etc/nginx/sites-available").mkdir(parents=True, exist_ok=True)
        Path("/etc/nginx/sites-enabled").mkdir(parents=True, exist_ok=True)
        Path("/etc/nginx/modules-enabled").mkdir(parents=True, exist_ok=True)

        Path(NGINX_VPN_CONF).write_text(
            _vpn_site_content(config.main_domain, config.cdn_domain, config.node_domain)
        )

        enabled_path = Path("/etc/nginx/sites-enabled/vpn")
        if not enabled_path.exists():
            enabled_path.symlink_to(NGINX_VPN_CONF)

        # Remove default configs that might conflict
        for p in ["/etc/nginx/sites-enabled/default", "/etc/nginx/conf.d/default.conf"]:
            path_obj = Path(p)
            if path_obj.exists():
                path_obj.unlink()

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
        checks["port_80"]   = "listening" if ":80 "            in ports else "MISSING"
        checks["port_443"]  = "listening" if ":443 "           in ports else "MISSING"
        checks["port_8443"] = "listening" if "127.0.0.1:8443 " in ports else "MISSING"
        checks["port_8444"] = "listening" if "127.0.0.1:8444 " in ports else "MISSING"

        passed = test_ok and active_ok and ":443 " in ports and "127.0.0.1:8444 " in ports
        return VerifyResult(passed=passed, checks=checks)