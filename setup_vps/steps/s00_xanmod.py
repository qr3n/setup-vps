from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_warning

class XanModStep(BaseStep):
    name = "s00_xanmod"
    title = "XanMod Kernel (BBR3)"
    description = "Install XanMod kernel for BBRv3 support (requires reboot, no Secure Boot)"

    def preflight(self, config, state) -> bool:
        uname = run_shell("uname -r", capture=True).stdout.strip()
        return "xanmod" in uname.lower()

    def run(self, config, state) -> StepResult:
        log = self.log_path()

        print_info("Checking architecture...")
        arch = run_shell("uname -m", capture=True).stdout.strip()
        if arch != "x86_64":
            return StepResult(success=False, message=f"XanMod only supports x86_64, but this is {arch}")

        print_info("Checking Secure Boot status...")
        sb = run_shell("mokutil --sb-state", capture=True)
        if "enabled" in sb.stdout.lower():
            return StepResult(success=False, message="Secure Boot is ENABLED. XanMod cannot be installed.")

        print_info("Adding XanMod repository...")
        cmds = [
            "wget -qO - https://dl.xanmod.org/archive.key | gpg --batch --yes --dearmor -o /usr/share/keyrings/xanmod-archive-keyring.gpg",
            'echo "deb [signed-by=/usr/share/keyrings/xanmod-archive-keyring.gpg] http://deb.xanmod.org releases main" | tee /etc/apt/sources.list.d/xanmod-release.list',
            "apt-get update"
        ]
        for cmd in cmds:
            r = run_shell(cmd, log_path=log)
            if r.returncode != 0:
                return StepResult(success=False, error=r.stderr, message=f"Failed to setup XanMod repo: {cmd}")

        print_info("Detecting CPU optimization level...")
        check_script = run_shell("curl -sL https://dl.xanmod.org/check_x86-64_psabi.sh | bash", capture=True)

        # Безопасный парсинг вывода (ищем именно подтверждение поддержки)
        out = check_script.stdout.lower()
        level = "v3"  # default safe-ish

        if "v4 (supported)" in out or "supports x86-64-v4" in out:
            level = "v4"
        elif "v3 (supported)" in out or "supports x86-64-v3" in out:
            level = "v3"
        elif "v2 (supported)" in out or "supports x86-64-v2" in out:
            level = "v2"
        elif "v1 (supported)" in out or "supports x86-64-v1" in out:
            level = "v1"

        # Используем пакет LTS-ветки, так как базовые метапакеты были удалены
        pkg_name = f"linux-xanmod-lts-x64{level}"
        print_info(f"Installing {pkg_name}...")

        env = {"DEBIAN_FRONTEND": "noninteractive"}
        r = run_shell(f"apt-get install -yq {pkg_name}", log_path=log, env=env)

        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr,
                              message=f"XanMod installation failed. Package '{pkg_name}' not found or install failed.")

        print_warning("XanMod kernel installed. REBOOT IS REQUIRED to activate BBR3.")
        return StepResult(success=True, message="XanMod installed. Please reboot the server.")

    def verify(self, config, state) -> VerifyResult:
        uname = run_shell("uname -r", capture=True).stdout.strip()
        is_xanmod = "xanmod" in uname.lower()
        return VerifyResult(passed=is_xanmod, checks={"kernel": uname})
