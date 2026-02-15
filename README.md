# GWS Homelab

Infrastructure as Code for a personal homelab managed entirely with Ansible.

## Inventory

| Name     | Device                          | IP              | MAC               | OS Image                                                  |
|----------|---------------------------------|-----------------|-------------------|-----------------------------------------------------------|
| rpi3     | Raspberry Pi 3 B+ (906 MiB)    | 192.168.1.145   | b8:27:eb:7c:73:db | ubuntu-24.04.4-preinstalled-server-arm64+raspi.img.xz     |
| optiplex | Dell OptiPlex 3040 Micro (i5-6500T, 6 GB, 240 GB SSD) | 192.168.1.150   | —                 | ubuntu-24.04.4-live-server-amd64.iso                      |

## Prerequisites

- macOS workstation with Homebrew
- Ansible ≥ 2.16
- SSH key already generated:

```bash
# Key already created at:
# ~/.ssh/id_ed25519_gws_homelab
```

## Quick Start

```bash
# 1. Install dependencies
make setup

# 2. Download OS images and build autoinstall ISO
./scripts/flash.sh download
./scripts/flash.sh build-iso           # repack OptiPlex ISO with autoinstall
./scripts/flash.sh list                # identify your SD/USB disk
./scripts/flash.sh rpi /dev/diskN      # flash RPi SD card
./scripts/flash.sh optiplex /dev/diskN # flash OptiPlex USB (autoinstall)

# 3. Boot devices, then run bootstrap (first-time setup)
make bootstrap

# 4. Apply full configuration
make deploy
```

## Project Structure

```
.
├── ansible.cfg              # Ansible configuration
├── inventory/
│   ├── hosts.yml            # Device inventory
│   └── group_vars/
│       └── all.yml          # Variables shared across all hosts
├── playbooks/
│   ├── bootstrap.yml        # First-time setup (user, SSH, sudo)
│   └── site.yml             # Full configuration playbook
├── roles/
│   └── common/              # Base hardening & packages
├── cloud-init/
│   ├── rpi/                 # Cloud-init for Raspberry Pi
│   └── optiplex/            # Autoinstall for OptiPlex
├── scripts/
│   └── flash.sh             # Image flashing helper
├── docs/
│   └── flashing.md          # Flashing instructions
├── ssh.config               # SSH config snippet
├── Makefile                 # Convenience targets
└── requirements.yml         # Ansible Galaxy requirements
```

## SSH Access

```bash
# Copy the snippet into your SSH config
cat ssh.config >> ~/.ssh/config

# Then connect with:
ssh rpi3
ssh optiplex
```

## License

MIT — personal homelab use.
