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
#   brew install xorriso
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
    for cmd in xorriso python3; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "ERROR: Missing required tools: ${missing[*]}"
        echo "Install with: brew install ${missing[*]}"
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

# Modify GRUB config to add autoinstall + nocloud datasource
# Uses a temp Python script to avoid bash/python escaping issues
inject_autoinstall_grub() {
    local grub_cfg="$1"
    cat > "$WORK_DIR/modify_grub.py" << 'PYEOF'
import sys

grub_cfg = sys.argv[1]
with open(grub_cfg, 'r') as f:
    content = f.read()

ds = 'ds=nocloud\\;s=/cdrom/nocloud/'

lines = content.split('\n')
new_lines = []
for line in lines:
    s = line.strip()
    if s.startswith('linux') and '/vmlinuz' in s:
        if 'autoinstall' not in line:
            if '---' in line:
                line = line.replace('---', 'autoinstall ' + ds + ' ---', 1)
            else:
                line = line.rstrip() + ' autoinstall ' + ds
    elif s.startswith('set timeout='):
        line = '\tset timeout=3'
    new_lines.append(line)

with open(grub_cfg, 'w') as f:
    f.write('\n'.join(new_lines))
PYEOF
    python3 "$WORK_DIR/modify_grub.py" "$grub_cfg"
}

# --- Main ---
echo "==> Building autoinstall ISO for OptiPlex..."
check_deps
check_source_iso
check_user_data

# Clean up any previous build
rm -rf "$WORK_DIR"
rm -f "$OUTPUT_ISO"
mkdir -p "$WORK_DIR"

# --- Extract full ISO so we can inspect and modify reliably ---
echo "==> Extracting source ISO..."
xorriso -osirrox on -indev "$SOURCE_ISO" -extract / "$WORK_DIR/iso" 2>/dev/null
chmod -R u+w "$WORK_DIR/iso"

# --- Inject autoinstall user-data ---
echo "==> Injecting autoinstall config..."
mkdir -p "$WORK_DIR/iso/autoinstall"
cp "${CLOUD_INIT_DIR}/user-data" "$WORK_DIR/iso/autoinstall/user-data"
touch "$WORK_DIR/iso/autoinstall/meta-data"

# Also place at /nocloud/ as a fallback for cloud-init nocloud datasource
mkdir -p "$WORK_DIR/iso/nocloud"
cp "${CLOUD_INIT_DIR}/user-data" "$WORK_DIR/iso/nocloud/user-data"
touch "$WORK_DIR/iso/nocloud/meta-data"

# --- Modify GRUB configs (both BIOS and UEFI paths) ---
echo "==> Modifying GRUB config to trigger autoinstall..."

GRUB_MODIFIED=0

# BIOS GRUB config
if [[ -f "$WORK_DIR/iso/boot/grub/grub.cfg" ]]; then
    echo "  Processing /boot/grub/grub.cfg (BIOS)..."
    inject_autoinstall_grub "$WORK_DIR/iso/boot/grub/grub.cfg"
    GRUB_MODIFIED=1
fi

# Some ISOs also have a loopback.cfg
if [[ -f "$WORK_DIR/iso/boot/grub/loopback.cfg" ]]; then
    echo "  Processing /boot/grub/loopback.cfg..."
    inject_autoinstall_grub "$WORK_DIR/iso/boot/grub/loopback.cfg"
fi

# --- Verify autoinstall injection ---
echo "==> Verifying GRUB modifications..."
VERIFY_OK=1

for cfg in "$WORK_DIR/iso/boot/grub/grub.cfg"; do
    if [[ -f "$cfg" ]]; then
        if grep -q 'autoinstall' "$cfg"; then
            echo "  ✓ $(basename "$cfg"): autoinstall parameter found"
            # Show the modified linux lines for confirmation
            grep -n 'linux.*autoinstall' "$cfg" | head -3 | while read -r line; do
                echo "    $line"
            done
        else
            echo "  ✗ $(basename "$cfg"): autoinstall parameter MISSING"
            VERIFY_OK=0
        fi
    fi
done

if [[ "$GRUB_MODIFIED" -eq 0 ]]; then
    echo "  ✗ ERROR: No GRUB config files found in ISO"
    exit 1
fi

# Verify autoinstall user-data is valid YAML with autoinstall key
if grep -q '^autoinstall:' "$WORK_DIR/iso/autoinstall/user-data"; then
    echo "  ✓ user-data: contains autoinstall key"
else
    echo "  ✗ user-data: missing 'autoinstall:' key"
    exit 1
fi

# --- Repack ISO preserving boot structure ---
echo "==> Repacking ISO..."
MAP_LOOPBACK=""
if [[ -f "$WORK_DIR/iso/boot/grub/loopback.cfg" ]]; then
    MAP_LOOPBACK="-map $WORK_DIR/iso/boot/grub/loopback.cfg /boot/grub/loopback.cfg"
fi

xorriso -indev "$SOURCE_ISO" \
    -outdev "$OUTPUT_ISO" \
    -map "$WORK_DIR/iso/autoinstall" /autoinstall \
    -map "$WORK_DIR/iso/nocloud" /nocloud \
    -map "$WORK_DIR/iso/boot/grub/grub.cfg" /boot/grub/grub.cfg \
    $MAP_LOOPBACK \
    -boot_image any replay \
    -end

# Clean up working directory
rm -rf "$WORK_DIR"

# --- Final summary ---
ISO_SIZE=$(du -h "$OUTPUT_ISO" | cut -f1)
echo ""
echo "✅ Autoinstall ISO built: ${OUTPUT_ISO} (${ISO_SIZE})"
echo ""
echo "Flash it with:"
echo "  ./scripts/flash.sh optiplex /dev/diskN"
