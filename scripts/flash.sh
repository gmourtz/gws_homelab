#!/usr/bin/env bash
# ------------------------------------------------------------------
# flash.sh — Flash OS images to SD cards / USB sticks on macOS
#
# Usage:
#   ./scripts/flash.sh list                  # show external disks
#   ./scripts/flash.sh rpi   /dev/diskN      # flash RPi SD card
#   ./scripts/flash.sh optiplex /dev/diskN   # flash OptiPlex USB
#   ./scripts/flash.sh download              # download both images
#
# Prerequisites: ensure images are downloaded to ./images/
# ------------------------------------------------------------------
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")/.." && pwd)/images"
CLOUD_INIT_DIR="$(cd "$(dirname "$0")/.." && pwd)/cloud-init"

# --- Image versions (Ubuntu 24.04.4 LTS — released 2026-02-12) ---
RPI_IMAGE="ubuntu-24.04.4-preinstalled-server-arm64+raspi.img.xz"
RPI_URL="https://cdimage.ubuntu.com/releases/24.04/release/${RPI_IMAGE}"

OPTIPLEX_ISO="ubuntu-24.04.4-live-server-amd64.iso"
OPTIPLEX_URL="https://releases.ubuntu.com/24.04/${OPTIPLEX_ISO}"
OPTIPLEX_AUTOINSTALL_ISO="optiplex-autoinstall.iso"

usage() {
    echo "Usage:"
    echo "  $0 list                 Show external/removable disks"
    echo "  $0 download             Download OS images to ./images/"
    echo "  $0 build-iso            Build autoinstall ISO for OptiPlex"
    echo "  $0 rpi   /dev/diskN     Flash RPi image to SD card"
    echo "  $0 optiplex /dev/diskN  Flash autoinstall ISO to USB stick"
    echo ""
    echo "Workflow:"
    echo "  1. $0 download          # fetch base images"
    echo "  2. $0 build-iso         # repack OptiPlex ISO with autoinstall"
    echo "  3. $0 list              # identify your SD/USB disk"
    echo "  4. $0 rpi /dev/diskN    # flash RPi SD card"
    echo "  5. $0 optiplex /dev/diskN  # flash OptiPlex USB"
    exit 1
}

# ------------------------------------------------------------------
# list — Show external disks so the user can identify their target
# ------------------------------------------------------------------
list_disks() {
    echo "==> External / removable disks detected:"
    echo ""
    # diskutil list shows all disks; filter for external ones
    diskutil list external 2>/dev/null || diskutil list | grep -A5 "external"
    echo ""
    echo "---"
    echo "Tip: look for your SD card / USB stick by size."
    echo "     Then use that /dev/diskN path with the flash command."
    echo ""
    echo "     For more detail on a specific disk:"
    echo "       diskutil info /dev/diskN"
}

# ------------------------------------------------------------------
# download — Fetch both OS images into ./images/
# ------------------------------------------------------------------
download_images() {
    mkdir -p "${IMAGE_DIR}"

    echo "==> Downloading images to ${IMAGE_DIR}/"
    echo ""

    if [[ -f "${IMAGE_DIR}/${RPI_IMAGE}" ]]; then
        echo "  RPi image already exists, skipping."
    else
        echo "  Downloading RPi image (~1.2 GB)..."
        curl -L --progress-bar -o "${IMAGE_DIR}/${RPI_IMAGE}" "${RPI_URL}"
    fi

    echo ""

    if [[ -f "${IMAGE_DIR}/${OPTIPLEX_ISO}" ]]; then
        echo "  OptiPlex ISO already exists, skipping."
    else
        echo "  Downloading OptiPlex ISO (~3 GB)..."
        curl -L --progress-bar -o "${IMAGE_DIR}/${OPTIPLEX_ISO}" "${OPTIPLEX_URL}"
    fi

    echo ""
    echo "✅ Images ready in ${IMAGE_DIR}/"
}

confirm() {
    echo ""
    echo "⚠️  WARNING: This will ERASE all data on $1"
    diskutil info "$1" 2>/dev/null | grep -E "Device / Media Name|Disk Size|Volume Name" || true
    echo ""
    read -rp "Type 'yes' to continue: " answer
    [[ "$answer" == "yes" ]] || { echo "Aborted."; exit 1; }
}

validate_disk() {
    local disk="$1"
    # Ensure the disk exists
    if ! diskutil info "$disk" &>/dev/null; then
        echo "ERROR: Disk $disk not found."
        echo "Run '$0 list' to see available disks."
        exit 1
    fi
    # Safety: refuse to write to the boot disk
    local boot_disk
    boot_disk=$(diskutil info / | grep "Part of Whole" | awk '{print $NF}')
    if [[ "$disk" == "/dev/${boot_disk}" ]]; then
        echo "ERROR: $disk is your boot disk! Refusing to overwrite."
        exit 1
    fi
}

flash_rpi() {
    local disk="$1"
    local rdisk="${disk/disk/rdisk}"  # use raw disk for speed

    echo "==> Flashing RPi image to ${disk}..."

    if [[ ! -f "${IMAGE_DIR}/${RPI_IMAGE}" ]]; then
        echo "ERROR: Image not found at ${IMAGE_DIR}/${RPI_IMAGE}"
        echo "Run: $0 download"
        exit 1
    fi

    validate_disk "$disk"
    confirm "$disk"

    echo "==> Unmounting ${disk}..."
    diskutil unmountDisk "$disk"

    echo "==> Writing image (this may take several minutes)..."
    xzcat "${IMAGE_DIR}/${RPI_IMAGE}" | sudo dd of="$rdisk" bs=4m status=progress

    echo "==> Syncing..."
    sync

    # Mount the system-boot partition to inject cloud-init
    echo "==> Waiting for partitions..."
    sleep 3
    diskutil mountDisk "$disk"

    BOOT_PATH="/Volumes/system-boot"

    if [[ -d "$BOOT_PATH" ]]; then
        echo "==> Injecting cloud-init config into system-boot..."
        cp "${CLOUD_INIT_DIR}/rpi/user-data" "${BOOT_PATH}/user-data"
        cp "${CLOUD_INIT_DIR}/rpi/network-config" "${BOOT_PATH}/network-config"
        echo "==> Cloud-init files written."
    else
        echo "⚠️  Could not find system-boot at ${BOOT_PATH}"
        echo "   Manually copy cloud-init/rpi/{user-data,network-config} to the boot partition."
    fi

    echo "==> Ejecting ${disk}..."
    diskutil eject "$disk"

    echo ""
    echo "✅ RPi SD card ready. Insert into Raspberry Pi and boot."
}

flash_optiplex() {
    local disk="$1"
    local rdisk="${disk/disk/rdisk}"
    local iso="${IMAGE_DIR}/${OPTIPLEX_AUTOINSTALL_ISO}"

    if [[ ! -f "$iso" ]]; then
        echo "ERROR: Autoinstall ISO not found at ${iso}"
        echo "Run: $0 download && $0 build-iso"
        exit 1
    fi

    echo "==> Flashing OptiPlex autoinstall ISO to ${disk}..."

    validate_disk "$disk"
    confirm "$disk"

    echo "==> Unmounting ${disk}..."
    diskutil unmountDisk "$disk"

    echo "==> Writing ISO (this may take several minutes)..."
    sudo dd if="$iso" of="$rdisk" bs=4m status=progress

    echo "==> Syncing..."
    sync

    echo "==> Ejecting ${disk}..."
    diskutil eject "$disk"

    echo ""
    echo "✅ OptiPlex USB ready. Plug in, power on, press F12, select USB."
    echo "   Install is fully unattended — walk away."
    echo "   After reboot: ssh gws@192.168.1.150"
}

# --- Main ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[[ $# -lt 1 ]] && usage

case "$1" in
    list)      list_disks ;;
    download)  download_images ;;
    build-iso) "${SCRIPT_DIR}/build-iso.sh" ;;
    rpi)       [[ $# -ne 2 ]] && usage; flash_rpi "$2" ;;
    optiplex)  [[ $# -ne 2 ]] && usage; flash_optiplex "$2" ;;
    *)         usage ;;
esac
