import os
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_box, console, print_success
from rich.prompt import Confirm
from rich.syntax import Syntax

DOCKER_COMPOSE_PATH = Path("/root/remnawave/docker-compose.yml")

DOCKER_COMPOSE_TEMPLATE = """services:
  remnanode:
    container_name: remnanode
    hostname: remnanode
    image: remnawave/node:latest
    network_mode: host
    restart: always
    cap_add:
      - NET_ADMIN
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    environment:
      - NODE_PORT=2222
      - SECRET_KEY="{secret_key}"
"""

class RemnawaveNodeStep(BaseStep):
    name = "s07_remnawave"
    title = "Remnawave Node"
    description = "Install Docker and deploy remnawave-node via docker-compose"

    def preflight(self, config, state) -> bool:
        if not DOCKER_COMPOSE_PATH.exists():
            return False
        r = run_shell("docker ps -q -f name=remnanode", capture=True)
        return r.stdout.strip() != ""

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        # 1. Install Docker if missing
        r = run_shell("docker --version", capture=True)
        if r.returncode != 0:
            print_info("Installing Docker...")
            run_shell("curl -fsSL https://get.docker.com -o get-docker.sh", log_path=log)
            run_shell("sh get-docker.sh", log_path=log)
            run_shell("rm get-docker.sh", log_path=log)

        # 2. Prepare directory
        DOCKER_COMPOSE_PATH.parent.mkdir(parents=True, exist_ok=True)

        # 3. Show instructions and wait for user
        print_box(
            "[bold cyan]Remnawave Panel Configuration[/bold cyan]",
            "Please add this node to your Remnawave Panel:\n\n"
            f"  [bold]Address:[/bold]  [cyan]{config.node_domain}[/cyan]\n"
            "  [bold]Port:[/bold]     [cyan]443[/cyan]\n"
            "  [bold]Type:[/bold]     [cyan]API[/cyan] (or Node)\n\n"
            "Steps:\n"
            "1. Go to [bold]Nodes[/bold] -> [bold]Add Node[/bold].\n"
            "2. Fill in the [bold]Address[/bold] and [bold]Port[/bold] shown above.\n"
            "3. After saving, the panel will provide a [bold]Secret Key[/bold] (Base64 string).\n"
            "4. Copy that key and paste it below.",
            style="cyan"
        )

        secret_key = ""
        while not secret_key:
            secret_key = console.input("[bold yellow]Enter the Secret Key from the panel: [/bold yellow]").strip()
            if not secret_key:
                print_info("Secret Key is required to continue.")

        # 4. Write docker-compose.yml
        content = DOCKER_COMPOSE_TEMPLATE.format(secret_key=secret_key)
        DOCKER_COMPOSE_PATH.write_text(content)
        print_info(f"Written docker-compose.yml to {DOCKER_COMPOSE_PATH}")

        # 5. Start the container
        print_info("Starting remnawave-node...")
        # Try docker compose (new) then docker-compose (old)
        r = run_shell("docker compose up -d", cwd=str(DOCKER_COMPOSE_PATH.parent), log_path=log)
        if r.returncode != 0:
            r = run_shell("docker-compose up -d", cwd=str(DOCKER_COMPOSE_PATH.parent), log_path=log)
        
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="Failed to start docker-compose")

        print_success("Remnawave node started successfully!")
        return StepResult(success=True, message="Remnawave node is running.")

    def verify(self, config, state) -> VerifyResult:
        checks = {}
        r = run_shell("docker ps -q -f name=remnanode", capture=True)
        is_running = r.stdout.strip() != ""
        checks["remnanode_running"] = "yes" if is_running else "no"
        
        # Check if port 2222 is listening
        ports = run_shell("ss -tlnp", capture=True).stdout
        checks["port_2222"] = "listening" if ":2222 " in ports else "MISSING"

        passed = is_running and ":2222 " in ports
        return VerifyResult(passed=passed, checks=checks)
