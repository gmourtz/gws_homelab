# GWS Homelab

Monorepo for a two-node homelab. Ubuntu Server + Docker, managed by Ansible.

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
make deploy       # configure hosts (packages, SSH, firewall, Docker)
make stacks       # deploy Docker Compose stacks
```

## Commands

```
make help       — show all commands
make ping       — test SSH connectivity
make deploy     — apply host configuration
make stacks     — deploy container stacks
make upgrade    — upgrade all packages
make check      — dry-run, no changes
```

## Repo Structure

```
.
├── apps/                        # App source code + Dockerfiles (built by CI)
├── stacks/                      # Docker Compose files (deployed by Ansible)
│   ├── optiplex/                #   stacks for the optiplex host
│   └── rpi3/                    #   stacks for the rpi3 host
├── playbooks/
│   ├── site.yml                 #   host config (packages, SSH, UFW, Docker)
│   └── deploy-stacks.yml        #   deploy stacks/ to hosts
├── inventory/
│   ├── hosts.yml                #   device inventory
│   ├── group_vars/all.yml       #   shared variables
│   └── host_vars/               #   per-host variables (UFW rules, etc.)
├── cloud-init/                  # First-boot config (RPi + OptiPlex)
├── scripts/                     # flash.sh, build-iso.sh
├── .github/workflows/           # CI: build + push images to GHCR
└── Makefile                     # make setup/deploy/stacks/upgrade
```

## SSH

```bash
cat ssh.config >> ~/.ssh/config
ssh rpi3
ssh optiplex
```
