#!/usr/bin/env bash
# ------------------------------------------------------------------
# build-iso.sh — Repack Ubuntu Server ISO with autoinstall config
#
# Takes the stock Ubuntu Server ISO and embeds the autoinstall
# user-data so the OptiPlex installs fully unattended.
#
# Usage:
#   ./scripts/build-iso.sh
#
# Prerequisites (macOS):
#   brew install xorriso cdrtools
#
# Output:
#   images/optiplex-autoinstall.iso
# ------------------------------------------------------------------
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE_DIR="${PROJECT_DIR}/images"
CLOUD_INIT_DIR="${PROJECT_DIR}/cloud-init/optiplex"
WORK_DIR="${IMAGE_DIR}/.build-iso"
SOURCE_ISO="${IMAGE_DIR}/ubuntu-24.04.4-live-server-amd64.iso"
OUTPUT_ISO="${IMAGE_DIR}/optiplex-autoinstall.iso"

# --- Preflight checks ---
check_deps() {
    local missing=()
    for cmd in xorriso; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "ERROR: Missing required tools: ${missing[*]}"
        echo "Install with:"
        echo "  brew install ${missing[*]}"
        exit 1
    fi
}

check_source_iso() {
    if [[ ! -f "$SOURCE_ISO" ]]; then
        echo "ERROR: Source ISO not found at ${SOURCE_ISO}"
        echo "Run first: ./scripts/flash.sh download"
        exit 1
    fi
}

check_user_data() {
    if [[ ! -f "${CLOUD_INIT_DIR}/user-data" ]]; then
        echo "ERROR: Autoinstall user-data not found at ${CLOUD_INIT_DIR}/user-data"
        exit 1
    fi
}

# --- Main ---
echo "==> Building autoinstall ISO for OptiPlex..."
check_deps
check_source_iso
check_user_data

# Clean up any previous build
rm -rf "$WORK_DIR"
rm -f "$OUTPUT_ISO"
mkdir -p "$WORK_DIR/autoinstall"

# Prepare autoinstall files
cp "${CLOUD_INIT_DIR}/user-data" "$WORK_DIR/autoinstall/user-data"
touch "$WORK_DIR/autoinstall/meta-data"

# Extract just the GRUB config to modify it
echo "==> Extracting GRUB config..."
xorriso -osirrox on -indev "$SOURCE_ISO" \
    -extract /boot/grub/grub.cfg "$WORK_DIR/grub.cfg" 2>/dev/null

echo "==> Modifying GRUB to auto-trigger install..."
# Add autoinstall to the default boot entry's linux line
sed -i.bak 's|---$|--- autoinstall|' "$WORK_DIR/grub.cfg"
# Set timeout to 3 seconds (enough to interrupt if needed)
sed -i.bak 's/^set timeout=.*/set timeout=3/' "$WORK_DIR/grub.cfg"
rm -f "$WORK_DIR/grub.cfg.bak"

echo "==> Repacking ISO (cloning original + injecting autoinstall)..."
# Clone the original ISO and inject our files — preserves boot structure perfectly
xorriso -indev "$SOURCE_ISO" \
    -outdev "$OUTPUT_ISO" \
    -map "$WORK_DIR/autoinstall" /autoinstall \
    -map "$WORK_DIR/grub.cfg" /boot/grub/grub.cfg \
    -boot_image any replay \
    -volid "UBUNTU-AUTOINSTALL" \
    -end

# Clean up working directory
rm -rf "$WORK_DIR"

ISO_SIZE=$(du -h "$OUTPUT_ISO" | cut -f1)
echo ""
echo "✅ Autoinstall ISO built: ${OUTPUT_ISO} (${ISO_SIZE})"
echo ""
echo "Flash it with:"
echo "  ./scripts/flash.sh optiplex /dev/diskN"
