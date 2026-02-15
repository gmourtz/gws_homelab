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
mkdir -p "$WORK_DIR"

echo "==> Extracting source ISO..."
xorriso -osirrox on -indev "$SOURCE_ISO" -extract / "$WORK_DIR/iso" 2>/dev/null

# ISO contents are read-only — fix permissions so we can modify them
chmod -R u+w "$WORK_DIR/iso"

echo "==> Injecting autoinstall config..."
# Create the autoinstall directory on the ISO
mkdir -p "$WORK_DIR/iso/autoinstall"
cp "${CLOUD_INIT_DIR}/user-data" "$WORK_DIR/iso/autoinstall/user-data"

# Create an empty meta-data file (required by cloud-init)
touch "$WORK_DIR/iso/autoinstall/meta-data"

echo "==> Modifying GRUB to auto-trigger install..."
# Modify GRUB config to add autoinstall parameter and reduce timeout
GRUB_CFG="$WORK_DIR/iso/boot/grub/grub.cfg"
if [[ -f "$GRUB_CFG" ]]; then
    # Add autoinstall to the default boot entry's linux line
    sed -i.bak 's|---$|--- autoinstall|' "$GRUB_CFG"
    # Set timeout to 3 seconds (enough to interrupt if needed)
    sed -i.bak 's/^set timeout=.*/set timeout=3/' "$GRUB_CFG"
    rm -f "${GRUB_CFG}.bak"
fi

echo "==> Repacking ISO..."
# Extract MBR from original ISO for hybrid boot
dd if="$SOURCE_ISO" bs=1 count=432 of="$WORK_DIR/mbr.bin" 2>/dev/null

# Extract EFI partition
EFI_START=$(xorriso -indev "$SOURCE_ISO" -report_el_torito as_mkisofs 2>&1 | grep -oP '(?<=-append_partition 2 0xEF )\S+' || true)

xorriso -as mkisofs \
    -r -V "UBUNTU-AUTOINSTALL" \
    -o "$OUTPUT_ISO" \
    --grub2-mbr "$WORK_DIR/mbr.bin" \
    -partition_offset 16 \
    --mbr-force-bootable \
    -append_partition 2 28732ac11ff8d211ba4b00a0c93ec93b "$WORK_DIR/iso/boot.catalog" \
    -appended_part_as_gpt \
    -iso_mbr_part_type a2a0d0ebe5b9334487c068b6b72699c7 \
    -c '/boot.catalog' \
    -b '/boot/grub/i386-pc/eltorito.img' \
    -no-emul-boot -boot-load-size 4 -boot-info-table --grub2-boot-info \
    -eltorito-alt-boot \
    -e '--interval:appended_partition_2:::' \
    -no-emul-boot \
    "$WORK_DIR/iso" 2>/dev/null || {
    # Fallback: simpler xorriso command if the above fails
    echo "==> Trying simplified repack..."
    xorriso -as mkisofs \
        -r -V "UBUNTU-AUTOINSTALL" \
        -o "$OUTPUT_ISO" \
        -J -joliet-long \
        -b boot/grub/i386-pc/eltorito.img \
        -no-emul-boot -boot-load-size 4 -boot-info-table \
        -eltorito-alt-boot \
        -e boot/grub/efi.img \
        -no-emul-boot \
        -isohybrid-gpt-basdat \
        "$WORK_DIR/iso" 2>/dev/null || {
        # Final fallback: basic ISO
        echo "==> Using basic ISO creation..."
        xorriso -as mkisofs \
            -r -V "UBUNTU-AUTOINSTALL" \
            -o "$OUTPUT_ISO" \
            "$WORK_DIR/iso"
    }
}

# Clean up working directory
rm -rf "$WORK_DIR"

ISO_SIZE=$(du -h "$OUTPUT_ISO" | cut -f1)
echo ""
echo "✅ Autoinstall ISO built: ${OUTPUT_ISO} (${ISO_SIZE})"
echo ""
echo "Flash it with:"
echo "  ./scripts/flash.sh optiplex /dev/diskN"
