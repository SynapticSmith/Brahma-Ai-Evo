#!/usr/bin/env bash
# ==============================================================================
# Brahma Evo - macOS & Linux Uninstaller
# ==============================================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${RED}"
echo "==============================================================="
echo "               BRAHMA EVO - UNINSTALL WIZARD                   "
echo "==============================================================="
echo -e "${NC}"

OS_TYPE="$(uname -s)"
INSTALL_DIR="$HOME/.brahma-evo"

# 1. Terminate running instances
echo -e "${YELLOW}[1/4] Stopping active Brahma Evo processes...${NC}"
pkill -f "main.py" 2>/dev/null || true
pkill -f "BrahmaEvo" 2>/dev/null || true

# 2. Remove Shortcuts / Application Bundles
echo -e "${YELLOW}[2/4] Removing system launchers & shortcuts...${NC}"
if [ "$OS_TYPE" = "Darwin" ]; then
    rm -rf "$HOME/Applications/Brahma Evo.app" 2>/dev/null || true
    echo "Removed macOS Application bundle."
else
    rm -f "$HOME/.local/share/applications/brahma-evo.desktop" 2>/dev/null || true
    rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/brahma-evo.ico" 2>/dev/null || true
    echo "Removed Linux desktop entry."
fi

# Remove CLI alias
rm -f "$HOME/.local/bin/brahma" 2>/dev/null || true

# 3. Prompt for user data removal
echo -e "${YELLOW}[3/4] User configuration options...${NC}"
read -p "Do you also want to remove personal data, history, and models (~/BrahmaAI)? [y/N]: " REMOVE_DATA
if [[ "$REMOVE_DATA" =~ ^[Yy]$ ]]; then
    rm -rf "$HOME/BrahmaAI" 2>/dev/null || true
    rm -rf "$HOME/.config/BrahmaAI" 2>/dev/null || true
    echo "Removed personal data and cache."
else
    echo "Preserved user data in ~/BrahmaAI."
fi

# 4. Remove installation files
echo -e "${YELLOW}[4/4] Removing application directory...${NC}"
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
fi

echo ""
echo -e "${GREEN}===============================================================${NC}"
echo -e "${GREEN}       Brahma Evo has been cleanly uninstalled!               ${NC}"
echo -e "${GREEN}===============================================================${NC}"
echo ""
