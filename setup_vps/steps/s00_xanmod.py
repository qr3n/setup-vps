import urllib.request
from pathlib import Path
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.runner import run_shell
from setup_vps.ui import print_info, print_warning
import xml.etree.ElementTree as ET

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

        print_info("Detecting CPU optimization level...")
        check_script = run_shell("curl -sL https://dl.xanmod.org/check_x86-64_psabi.sh | bash", capture=True)

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

        print_info(f"Fetching latest XanMod LTS (x64{level}) from SourceForge...")
        rss_url = "https://sourceforge.net/projects/xanmod/rss?path=/releases/lts"
        try:
            req = urllib.request.urlopen(rss_url)
            root = ET.fromstring(req.read())
        except Exception as e:
            return StepResult(success=False, message=f"Failed to fetch XanMod RSS: {e}")

        # Собираем все ссылки на файлы из RSS-ленты
        links = [item.find('link').text for item in root.findall('./channel/item') if item.find('link') is not None]

        # Ищем свежий linux-image под наш уровень CPU
        image_link = next((l for l in links if "linux-image-" in l and f"x64{level}" in l), None)
        if not image_link:
            return StepResult(success=False, message=f"Could not find linux-image for x64{level} in RSS.")

        # Вытаскиваем директорию версии (например, 6.6.44-xanmod1), чтобы найти подходящие headers
        version_dir = image_link.split('/releases/lts/')[1].split('/')[0]

        # Ищем linux-headers (сначала с привязкой к уровню CPU, затем общие для этой версии)
        headers_link = next(
            (l for l in links if "linux-headers-" in l and f"/lts/{version_dir}/" in l and f"x64{level}" in l), None)
        if not headers_link:
            headers_link = next((l for l in links if "linux-headers-" in l and f"/lts/{version_dir}/" in l), None)

        if not headers_link:
            return StepResult(success=False, message="Could not find matching linux-headers in RSS.")

        print_info("Downloading kernel packages...")
        r1 = run_shell(f"wget -qO /tmp/xanmod-image.deb {image_link}", log_path=log)
        r2 = run_shell(f"wget -qO /tmp/xanmod-headers.deb {headers_link}", log_path=log)

        if r1.returncode != 0 or r2.returncode != 0:
            return StepResult(success=False, message="Failed to download .deb files from SourceForge.")

        print_info(f"Installing XanMod x64{level}...")
        env = {"DEBIAN_FRONTEND": "noninteractive"}

        # apt-get install отлично умеет ставить локальные .deb файлы и сам подтянет зависимости
        r = run_shell("apt-get install -yq /tmp/xanmod-image.deb /tmp/xanmod-headers.deb", log_path=log, env=env)

        # Уборка мусора
        run_shell("rm -f /tmp/xanmod-image.deb /tmp/xanmod-headers.deb", log_path=log)

        if r.returncode != 0:
            return StepResult(success=False, error=r.stderr, message="XanMod installation failed via apt/dpkg.")

        print_warning("XanMod kernel installed. REBOOT IS REQUIRED to activate BBR3.")
        return StepResult(success=True, message="XanMod installed. Please reboot the server.", reboot_required=True)

    def verify(self, config, state) -> VerifyResult:
        uname = run_shell("uname -r", capture=True).stdout.strip()
        is_xanmod = "xanmod" in uname.lower()
        return VerifyResult(passed=is_xanmod, checks={"kernel": uname})
