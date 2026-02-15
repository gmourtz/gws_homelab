# GWS Homelab

Ansible-managed homelab. Ubuntu Server + Docker on all hosts.

## Hosts

| Name     | Device                 | IP            | Image                                               |
|----------|------------------------|---------------|------------------------------------------------------|
| rpi3     | Raspberry Pi 3 B+      | 192.168.1.145 | ubuntu-24.04.4-preinstalled-server-arm64+raspi.img.xz |
| optiplex | Dell OptiPlex 3040     | 192.168.1.150 | ubuntu-24.04.4-live-server-amd64.iso                  |

## Setup

```bash
# 1. Install Ansible
make setup

# 2. Flash OS images
./scripts/flash.sh download              # download images
./scripts/flash.sh build-iso             # build autoinstall ISO for OptiPlex
./scripts/flash.sh list                  # find your SD/USB disk
./scripts/flash.sh rpi /dev/diskN        # flash RPi SD card
./scripts/flash.sh optiplex /dev/diskN   # flash OptiPlex USB

# 3. Boot devices, then:
make bootstrap    # first-time setup (once)
make deploy       # apply full config (repeat anytime)
```

## Commands

```
make ping       — test SSH connectivity
make deploy     — apply configuration
make upgrade    — upgrade all packages
make check      — dry-run, no changes
```

## SSH

```bash
cat ssh.config >> ~/.ssh/config
ssh rpi3
ssh optiplex
```
