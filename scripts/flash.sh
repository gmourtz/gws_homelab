#!/usr/bin/env bash
# ------------------------------------------------------------------
# flash.sh — Flash OS images to SD cards / USB sticks on macOS
#
# Usage:
#   ./scripts/flash.sh list                  # show external disks
#   ./scripts/flash.sh rpi3  /dev/diskN      # flash RPi 3 SD card
#   ./scripts/flash.sh kali  /dev/diskN      # flash Kali (RPi 5) SD card
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

# --- Kali Linux ARM Pi image (used for the kali host — RPi 5 pentesting lab) ---
# Bump this when a newer release is out: https://www.kali.org/get-kali/#kali-arm
KALI_VERSION="2026.1"
KALI_IMAGE="kali-linux-${KALI_VERSION}-raspberry-pi-arm64.img.xz"
KALI_URL="https://kali.download/arm-images/kali-${KALI_VERSION}/${KALI_IMAGE}"

usage() {
    echo "Usage:"
    echo "  $0 list                 Show external/removable disks"
    echo "  $0 download             Download OS images to ./images/"
    echo "  $0 build-iso <host>     Build autoinstall ISO (optiplex, openclaw)"
    echo "  $0 rpi3     /dev/diskN  Flash RPi 3 image to SD card (Ubuntu)"
    echo "  $0 kali     /dev/diskN  Flash Kali Linux image to RPi 5 SD card (lab box)"
    echo "  $0 optiplex /dev/diskN  Flash optiplex autoinstall ISO to USB stick"
    echo "  $0 openclaw /dev/diskN  Flash openclaw autoinstall ISO to USB stick"
    echo "  $0 localllm /dev/diskN  Flash localllm autoinstall ISO to USB stick"
    echo ""
    echo "Workflow:"
    echo "  1. $0 download             # fetch base images"
    echo "  2. $0 build-iso <host>     # repack ISO with autoinstall (optiplex, openclaw)"
    echo "  3. $0 list                 # identify your SD/USB disk"
    echo "  4. $0 rpi3 /dev/diskN      # flash RPi 3 SD card (or kali for the Pi 5)"
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
        echo "  RPi (Ubuntu) image already exists, skipping."
    else
        echo "  Downloading RPi (Ubuntu) image (~1.2 GB)..."
        curl -L --progress-bar -o "${IMAGE_DIR}/${RPI_IMAGE}" "${RPI_URL}"
    fi

    echo ""

    if [[ -f "${IMAGE_DIR}/${KALI_IMAGE}" ]]; then
        echo "  Kali ARM image already exists, skipping."
    else
        echo "  Downloading Kali ARM image (~3 GB)..."
        curl -L --progress-bar -o "${IMAGE_DIR}/${KALI_IMAGE}" "${KALI_URL}"
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
    local host="$1"        # rpi3 — also the cloud-init subdir name
    local disk="$2"
    local rdisk="${disk/disk/rdisk}"  # use raw disk for speed

    echo "==> Flashing ${host} image to ${disk}..."

    if [[ ! -f "${IMAGE_DIR}/${RPI_IMAGE}" ]]; then
        echo "ERROR: Image not found at ${IMAGE_DIR}/${RPI_IMAGE}"
        echo "Run: $0 download"
        exit 1
    fi

    if [[ ! -d "${CLOUD_INIT_DIR}/${host}" ]]; then
        echo "ERROR: cloud-init dir not found at ${CLOUD_INIT_DIR}/${host}"
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
        cp "${CLOUD_INIT_DIR}/${host}/user-data" "${BOOT_PATH}/user-data"
        cp "${CLOUD_INIT_DIR}/${host}/network-config" "${BOOT_PATH}/network-config"
        echo "==> Cloud-init files written."
    else
        echo "⚠️  Could not find system-boot at ${BOOT_PATH}"
        echo "   Manually copy cloud-init/${host}/{user-data,network-config} to the boot partition."
    fi

    echo "==> Ejecting ${disk}..."
    diskutil eject "$disk"

    echo ""
    echo "✅ ${host} SD card ready. Insert into Raspberry Pi and boot."
}

flash_kali() {
    # Flash the Kali ARM Pi image and inject cloud-init config.
    # Kali 2026+ uses ds=nocloud — reads user-data/network-config from the BOOT partition.
    # cloud-init creates the gws user + SSH key so site-kali.yml needs no sshpass.
    local disk="$1"
    local rdisk="${disk/disk/rdisk}"

    echo "==> Flashing Kali (${KALI_VERSION}) to ${disk}..."

    if [[ ! -f "${IMAGE_DIR}/${KALI_IMAGE}" ]]; then
        echo "ERROR: Kali image not found at ${IMAGE_DIR}/${KALI_IMAGE}"
        echo "Run: $0 download"
        exit 1
    fi

    if [[ ! -d "${CLOUD_INIT_DIR}/kali" ]]; then
        echo "ERROR: cloud-init dir not found at ${CLOUD_INIT_DIR}/kali"
        exit 1
    fi

    validate_disk "$disk"
    confirm "$disk"

    echo "==> Unmounting ${disk}..."
    diskutil unmountDisk "$disk"

    echo "==> Writing image (this may take several minutes)..."
    xzcat "${IMAGE_DIR}/${KALI_IMAGE}" | sudo dd of="$rdisk" bs=4m status=progress

    echo "==> Syncing..."
    sync

    echo "==> Waiting for partitions..."
    sleep 3
    diskutil mountDisk "$disk"

    BOOT_PATH="/Volumes/BOOT"

    if [[ -d "$BOOT_PATH" ]]; then
        echo "==> Injecting cloud-init config into BOOT..."
        cp "${CLOUD_INIT_DIR}/kali/user-data"     "${BOOT_PATH}/user-data"
        cp "${CLOUD_INIT_DIR}/kali/network-config" "${BOOT_PATH}/network-config"
        echo "==> Cloud-init files written."
    else
        echo "⚠️  Could not find BOOT at ${BOOT_PATH}"
        echo "   Manually copy cloud-init/kali/{user-data,network-config} to the BOOT partition."
    fi

    echo "==> Ejecting ${disk}..."
    diskutil eject "$disk"

    cat <<EOF

✅ Kali SD card ready.

Next steps:
  1. Plug the SD card into the Pi
  2. Plug Pi into MikroTik ether5 (VLAN 50 access port)
  3. Power on. Cloud-init runs (~2 min), then the Pi reboots automatically.
  4. After reboot, MikroTik assigns 192.168.50.11. Wait ~1 min, then:
       ssh-keygen -R 192.168.50.11
       ansible-playbook playbooks/site-kali.yml
  5. After bootstrap: make stacks → deploys vulnerable lab targets.

EOF
}

flash_x86() {
    local host="$1"
    local disk="$2"
    local rdisk="${disk/disk/rdisk}"
    local iso="${IMAGE_DIR}/${host}-autoinstall.iso"

    if [[ ! -f "$iso" ]]; then
        echo "ERROR: Autoinstall ISO not found at ${iso}"
        echo "Run: $0 build-iso ${host}"
        exit 1
    fi

    echo "==> Flashing ${host} autoinstall ISO to ${disk}..."

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
    echo "✅ ${host} USB ready. Plug in, power on, select USB boot."
    echo "   Install is fully unattended — walk away."
}

# --- Main ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[[ $# -lt 1 ]] && usage

case "$1" in
    list)      list_disks ;;
    download)  download_images ;;
    build-iso) [[ $# -ne 2 ]] && usage; "${SCRIPT_DIR}/build-iso.sh" "$2" ;;
    rpi3)      [[ $# -ne 2 ]] && usage; flash_rpi rpi3 "$2" ;;
    kali)      [[ $# -ne 2 ]] && usage; flash_kali     "$2" ;;  # Kali on the RPi 5 — see flash_kali
    optiplex)  [[ $# -ne 2 ]] && usage; flash_x86 optiplex "$2" ;;
    openclaw)  [[ $# -ne 2 ]] && usage; flash_x86 openclaw "$2" ;;
    localllm)  [[ $# -ne 2 ]] && usage; flash_x86 localllm "$2" ;;
    *)         usage ;;
esac
