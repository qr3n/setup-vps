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

        print_info("Checking Secure Boot status...")
        sb = run_shell("mokutil --sb-state", capture=True)
        if "enabled" in sb.stdout.lower():
            return StepResult(success=False, message="Secure Boot is ENABLED. XanMod cannot be installed.")

        print_info("Adding XanMod repository...")
        cmds = [
            "wget -qO - https://dl.xanmod.org/archive.key | gpg --dearmor -o /usr/share/keyrings/xanmod-archive-keyring.gpg",
            'echo "deb [signed-by=/usr/share/keyrings/xanmod-archive-keyring.gpg] http://deb.xanmod.org releases main" | tee /etc/apt/sources.list.d/xanmod-release.list',
            "apt-get update -qq"
        ]
        for cmd in cmds:
            run_shell(cmd, log_path=log)

        print_info("Detecting CPU optimization level...")
        # Simple detection based on research
        check_script = run_shell("curl -sL https://dl.xanmod.org/check_x86-64_psabi.sh | bash", capture=True)
        level = "v3" # default safe-ish
        if "v4" in check_script.stdout: level = "v4"
        elif "v3" in check_script.stdout: level = "v3"
        elif "v2" in check_script.stdout: level = "v2"
        
        print_info(f"Installing linux-xanmod-x64{level}...")
        env = {"DEBIAN_FRONTEND": "noninteractive"}
        r = run_shell(f"apt-get install -yq linux-xanmod-x64{level}", log_path=log, env=env)
        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="XanMod installation failed")

        print_warning("XanMod kernel installed. REBOOT IS REQUIRED to activate BBR3.")
        return StepResult(success=True, message="XanMod installed. Please reboot the server.")

    def verify(self, config, state) -> VerifyResult:
        uname = run_shell("uname -r", capture=True).stdout.strip()
        is_xanmod = "xanmod" in uname.lower()
        return VerifyResult(passed=is_xanmod, checks={"kernel": uname})
