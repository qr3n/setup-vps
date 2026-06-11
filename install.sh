#!/bin/bash

# setup-vps Installer
# Target: Ubuntu 22.04+ / Debian 12+

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}==============================================${NC}"
echo -e "${CYAN}      VPS Setup CLI Installer — 2026          ${NC}"
echo -e "${CYAN}==============================================${NC}"

# 1. Check root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run as root (use sudo)${NC}"
  exit 1
fi

# 2. Install system dependencies
echo -e "\n${GREEN}[1/4] Installing system dependencies...${NC}"
apt-get update -qq
apt-get install -y python3 python3-venv git curl jq > /dev/null

# 3. Setup directory and clone
INSTALL_DIR="/opt/setup-vps"
REPO_URL="https://github.com/YOUR_USERNAME/setup-vps.git" # ЗАМЕНИТЕ ПОСЛЕ СОЗДАНИЯ РЕПО

echo -e "\n${GREEN}[2/4] Cloning repository to ${INSTALL_DIR}...${NC}"
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${CYAN}Directory exists, updating...${NC}"
    cd "$INSTALL_DIR"
    git pull
else
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 4. Setup Virtual Environment
echo -e "\n${GREEN}[3/4] Setting up Python virtual environment...${NC}"
python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e .

# 5. Create global symlink
echo -e "\n${GREEN}[4/4] Creating global command...${NC}"
cat > /usr/local/bin/setup-vps << EOF
#!/bin/bash
cd $INSTALL_DIR
source .venv/bin/activate
exec setup-vps "\$@"
EOF
chmod +x /usr/local/bin/setup-vps

echo -e "\n${GREEN}==============================================${NC}"
echo -e "${GREEN}      Installation Complete!                  ${NC}"
echo -e "  Run it anytime by typing: ${CYAN}setup-vps${NC}"
echo -e "${GREEN}==============================================${NC}"

# Optional: run immediately
if [[ "$1" == "--run" ]]; then
    setup-vps
fi
