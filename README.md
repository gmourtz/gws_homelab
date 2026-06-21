# GWS Homelab

A personal homelab managed entirely as code. Two dozen self-hosted services run across four low-power machines, all defined in Ansible and Docker Compose, and the whole setup can be rebuilt from a wiped disk with a single reflash.

It runs the things I'd otherwise rent from a cloud provider: media and photo libraries, document scanning, network-wide ad-blocking, a local LLM, an autonomous AI agent, monitoring, and backups. Everything runs on Ubuntu and Docker, on second-hand mini-PCs and Raspberry Pis, behind a MikroTik router that firewalls the network into segmented VLANs. It also serves my personal site at [gmourtzinos.com](https://gmourtzinos.com).

The goal was to run production-shaped systems end to end (networking, containers, CI/CD, security, and AI) on a small budget, with everything reproducible and documented in code rather than in my head.

Lately a lot of that is AI. The homelab serves its own LLM on local hardware and runs agents on it, so the models, and the data they touch, never leave the house.

## What's running

The full list renders on an internal dashboard (`https://homepage.internal`). Highlights:

- **Media:** Jellyfin, Audiobookshelf, Immich (photos), Navidrome, Jellyseerr
- **Productivity:** Paperless-ngx, Stirling-PDF, IT-Tools, Draw.io, Actual Budget
- **AI:** Open WebUI (`chat.internal`), Ollama (`localllm.internal:11434`), OpenClaw autonomous agent
- **Monitoring:** Beszel (per-host metrics), Dozzle (logs), Uptime Kuma (uptime + Telegram alerts), Pi-hole
- **Infrastructure:** Caddy reverse proxy (internal CA, `*.internal` HTTPS), Cloudflare Tunnel, Sablier (scale-to-zero)

## Local and private AI

The piece I'm most into right now: the homelab runs its own AI on its own hardware, and the agents built on top keep everything in-house.

- **Self-hosted inference.** A dedicated node (`localllm`: i7-13700T, 16 GB, CPU-only) runs [Ollama](https://ollama.com) behind an OpenAI-compatible API, with [Open WebUI](https://openwebui.com) for browser chat at `chat.internal`. Models live in a local volume and the API is firewalled to the trusted VLAN, so prompts and context never leave the network.
- **Agents running on local AI.** My custom agents reach the local model through the standard OpenAI SDK, just pointed at a different `base_url`. The portfolio agent pulls my real brokerage positions, runs the analysis on a local `qwen3` model with structured outputs, and sends me the verdict over Telegram, with no financial data leaving the network and no per-token bill.
- **Pluggable models.** Every agent speaks the OpenAI-compatible API, so swapping between the local model and a frontier cloud one is a single environment variable. The always-on autonomous agent (OpenClaw) runs on its own firewalled VLAN with a destination-only Tailscale tag, so it can be reached but can't reach back out.
- **Why it matters.** Self-hosted means no rate limits, no recurring inference bill, and full control of the stack from the silicon up.

## How it works

A few principles, each enforced by the repo rather than by memory:

- **Everything as code.** Ansible converges host configuration, one Docker Compose file per host defines its services, and the MikroTik router is configured over its API. No click-ops.
- **Reproducible from scratch.** OS installs are fully unattended: cloud-init on the Raspberry Pis, a repacked autoinstall ISO on the x86 boxes. A wiped machine boots back SSH-ready with no manual steps, and `make deploy && make stacks` brings the rest up.
- **Monorepo.** Infrastructure, compose stacks, and the source for custom apps all live here. Path-filtered GitHub Actions build multi-arch (amd64 and arm64) images and push them to GHCR, so only what changed rebuilds.
- **Config-driven.** Adding a service, a DNS record, or putting a container behind scale-to-zero is a one-line change to a vars file, not a new playbook.
- **Segmented by trust.** VLANs separate the trusted network from the autonomous agent, and a Tailscale mesh with per-host ACL tags lets the agent be reached over the tailnet without letting it open connections outward.

## Hosts

| Name     | Hardware                                                  | IP             | VLAN           | Role                                         |
|----------|-----------------------------------------------------------|----------------|----------------|----------------------------------------------|
| rpi3     | Raspberry Pi 3 B+ · 1 GB RAM                              | 192.168.10.10  | 10 (Trusted)   | Pi-hole DNS and network-wide ad-blocking     |
| optiplex | Dell OptiPlex 3040 Micro · i5-6500T · 6 GB · 240 GB SSD   | 192.168.10.20  | 10 (Trusted)   | Apps, media, Caddy reverse proxy, Beszel hub |
| localllm | Dell OptiPlex Micro Plus 7010 · i7-13700T · 16 GB DDR5    | 192.168.10.30  | 10 (Trusted)   | Ollama and Open WebUI (CPU inference)        |
| openclaw | Lenovo laptop · i5-8265U · 8 GB                           | 192.168.40.30  | 40 (Agent)     | Autonomous AI agent, VLAN-isolated           |
| mikrotik | hAP ac²                                                   | 192.168.10.1   | gateway        | VLAN router, firewall, DHCP, WiFi AP         |

## Design decisions

Some choices and the reasoning behind them:

- **Docker Compose instead of Kubernetes.** A few nodes on different CPU architectures, including a 1 GB Pi, mean there's no failover worth a control plane. RAM goes to workloads, not orchestration.
- **Local LLM on CPU.** `localllm` (i7-13700T, 16 GB) serves Ollama and Open WebUI with the context window capped so the KV cache fits in RAM. There's no GPU, and working within that limit is part of the point.
- **A sandboxed AI agent.** OpenClaw runs on its own VLAN and carries a destination-only Tailscale tag, so even a misbehaving agent can't reach the rest of the network or open connections across the tailnet.
- **Scale-to-zero.** Sablier and a Caddy plugin stop idle containers and start them again on the first HTTP request, so rarely-used services cost nothing at rest.

## Setup

```bash
# 1. Install Ansible, collections, and Python deps
make setup

# 2. Flash OS images (per host)
./scripts/flash.sh download                     # download base images
./scripts/flash.sh build-iso <host>             # build autoinstall ISO (optiplex, openclaw, localllm)
./scripts/flash.sh list                         # find the SD/USB device path
./scripts/flash.sh rpi      /dev/diskN          # flash a Raspberry Pi SD card
./scripts/flash.sh optiplex /dev/diskN          # flash an x86 host's USB

# 3. Boot devices (autoinstall is unattended), then:
make routeros                                   # configure MikroTik (DHCP leases, VLANs, firewall)
make deploy                                     # site.yml: packages, SSH, UFW, fail2ban, Docker, Tailscale
make stacks                                     # deploy-stacks.yml: pull and run all per-host compose stacks
```

All hosts share one SSH key (`~/.ssh/id_ed25519_gws_homelab`) and user (`gws`). Password and root login are disabled. Convenience aliases live in `ssh.config` (`cat ssh.config >> ~/.ssh/config`, then `ssh optiplex`).

## Make targets

| Target          | Action |
|-----------------|--------|
| `make setup`    | Install Ansible Galaxy collections and Python deps |
| `make ping`     | SSH reachability test for all hosts |
| `make deploy`   | Run `playbooks/site.yml` (host-level config) |
| `make stacks`   | Run `playbooks/deploy-stacks.yml` (Docker stacks and Uptime Kuma post-deploy) |
| `make routeros` | Run `playbooks/configure-routeros.yml` (MikroTik via API) |
| `make vault`    | Edit the ansible-vault encrypted secrets file |
| `make test`     | `pytest` for any `apps/*` with a `pytest.ini` |

## Workflow

```
apps/<name>/  ->  GitHub Actions (path-filtered)  ->  ghcr.io/gmourtz/<name>:tag
                                                          |
stacks/<host>.yml  ->  ansible-playbook deploy-stacks.yml  ->  running on host
```

- Edit code in `apps/`, and CI builds and pushes the image (multi-arch)
- Edit infra in `stacks/`, `inventory/`, or `playbooks/`, then run `make stacks` or `make deploy`
- Edit MikroTik config in `inventory/host_vars/mikrotik.yml`, then run `make routeros`

## Repo structure

```
.
├── apps/                # Custom app source and Dockerfiles (CI builds and pushes to GHCR)
├── stacks/              # Docker Compose files, one per host
├── playbooks/           # site.yml (hosts), deploy-stacks.yml (services), configure-routeros.yml
├── inventory/           # hosts, group/host vars, ansible-vault encrypted secrets
├── cloud-init/          # First-boot autoinstall and cloud-init configs (unattended OS setup)
├── scripts/             # flash.sh (image flashing), build-iso.sh (autoinstall ISO repack)
├── .github/workflows/   # CI: path-filtered multi-arch build and push to GHCR
└── Makefile
```

All secrets are stored encrypted with Ansible Vault and referenced as variables, so nothing sensitive is committed in plaintext.
