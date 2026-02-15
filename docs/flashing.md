# Flashing OS Images

## Download Images

```bash
mkdir -p images

# Or use the flash script to download both:
./scripts/flash.sh download

# Manual download if preferred:
# Raspberry Pi 3 B+ — Ubuntu 24.04.4 LTS Server ARM64
curl -L -o images/ubuntu-24.04.4-preinstalled-server-arm64+raspi.img.xz \
  https://cdimage.ubuntu.com/releases/24.04/release/ubuntu-24.04.4-preinstalled-server-arm64+raspi.img.xz

# Dell OptiPlex — Ubuntu 24.04.4 LTS Server AMD64
curl -L -o images/ubuntu-24.04.4-live-server-amd64.iso \
  https://releases.ubuntu.com/24.04/ubuntu-24.04.4-live-server-amd64.iso
```

## Identify Target Disk (macOS)

Insert the micro SD card or USB stick, then:

```bash
# Use the built-in helper to show only external disks:
./scripts/flash.sh list

# Or use diskutil directly:
diskutil list external
```

Look for your device by **size** (e.g., 32 GB for SD, 16 GB for USB). **Double-check the disk number** — writing to the wrong disk will destroy data. The flash script includes a safety check that refuses to write to your boot disk.

## Flash

```bash
# 1. Download base images
./scripts/flash.sh download

# 2. Build autoinstall ISO for OptiPlex (requires xorriso)
brew install xorriso
./scripts/flash.sh build-iso

# 3. Flash Raspberry Pi (micro SD)
./scripts/flash.sh rpi /dev/disk4

# 4. Flash OptiPlex (USB stick) — uses autoinstall ISO
./scripts/flash.sh optiplex /dev/disk5
```

The RPi script automatically injects cloud-init config into the boot partition. On first boot, the Pi will:
- Create user `gws` with your SSH key
- Set hostname to `rpi3`
- Configure a static IP at `192.168.1.145`
- Enable SSH

The OptiPlex autoinstall ISO performs a fully unattended install:
- Creates user `gws` with your SSH key
- Sets hostname to `optiplex`
- Configures a static IP at `192.168.1.150`
- Uses the entire 240 GB SSD
- Enables SSH, disables password auth
- Reboots automatically when done

## OptiPlex Install

With the autoinstall ISO, the install is **fully hands-off**:

1. Plug USB into OptiPlex
2. Power on, press **F12** for boot menu, select USB
3. Walk away — the installer completes automatically
4. After reboot, OptiPlex is available at `192.168.1.150`
5. Test: `ssh gws@192.168.1.150`

## After Flashing

Once both devices are booted and reachable via SSH:

```bash
# Test connectivity
make ping

# Run bootstrap (first-time hardening)
make bootstrap

# Full deploy
make deploy
```
