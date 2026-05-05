# GWS Homelab

Monorepo for a four-node homelab. Ubuntu Server + Docker, managed by Ansible. MikroTik VLAN router enforces network segmentation; Caddy on optiplex serves all `*.internal` HTTPS.

## Hosts

| Name     | Hardware                                                  | IP             | VLAN            | Role                                            |
|----------|-----------------------------------------------------------|----------------|-----------------|-------------------------------------------------|
| rpi3     | Raspberry Pi 3 B+ · 1 GB RAM                              | 192.168.10.10  | 10 (Trusted)    | Pi-hole DNS + ad-blocking                       |
| optiplex | Dell OptiPlex 3040 Micro · i5-6500T · 6 GB · 240 GB SSD   | 192.168.10.20  | 10 (Trusted)    | Apps, media, Caddy reverse proxy, Beszel hub    |
| openclaw | Lenovo laptop · i5-8265U · 8 GB                           | 192.168.40.30  | 40 (Agent)      | Autonomous AI agent — VLAN-isolated             |
| localllm | Dell OptiPlex Micro Plus 7010 · i7-13700T · 16 GB DDR5 · 256 GB NVMe | 192.168.10.30 | 10 (Trusted) | Ollama (gemma4:e4b) + Open WebUI                |
| mikrotik | hAP ac²                                                    | 192.168.10.1   | gateway         | VLAN router, firewall, DHCP, WiFi AP            |

See [`docs/network-design.md`](docs/network-design.md) for the full VLAN/firewall design.

## Setup

```bash
# 1. Install Ansible + collections + Python deps
make setup

# 2. Flash OS images (per-host)
./scripts/flash.sh download                     # download base images
./scripts/flash.sh build-iso <host>             # build autoinstall ISO (optiplex, openclaw, localllm)
./scripts/flash.sh list                         # find the SD/USB device path
./scripts/flash.sh rpi      /dev/diskN          # flash RPi SD card
./scripts/flash.sh optiplex /dev/diskN          # flash OptiPlex USB
./scripts/flash.sh openclaw /dev/diskN          # flash openclaw USB
./scripts/flash.sh localllm /dev/diskN          # flash localllm USB

# 3. Boot devices (autoinstall is unattended), then:
make routeros                                   # configure MikroTik (DHCP leases, VLANs, firewall)
make deploy                                     # site.yml — packages, SSH, UFW, fail2ban, Docker, Tailscale, /etc/hosts
make stacks                                     # deploy-stacks.yml — pull/run all per-host compose stacks
```

## Make targets

| Target          | Action |
|-----------------|--------|
| `make setup`    | Install Ansible Galaxy collections + uptime-kuma-api Python dep |
| `make ping`     | SSH reachability test for all hosts |
| `make deploy`   | Run `playbooks/site.yml` (host-level config) |
| `make stacks`   | Run `playbooks/deploy-stacks.yml` (Docker stacks + Uptime Kuma post-deploy) |
| `make routeros` | Run `playbooks/configure-routeros.yml` (MikroTik via API) |
| `make vault`    | `ansible-vault edit inventory/group_vars/all/vault.yml` |
| `make test`     | `pytest` for any `apps/*` with a `pytest.ini` |

## Repo structure

```
.
├── apps/                        # Custom app source code + Dockerfiles (CI builds → GHCR)
│   ├── youtube_premium/         #   YouTube playlist downloader
│   ├── portfolio_agent/         #   Trading-212 + OpenAI portfolio analysis
│   ├── georgios-website/        #   Static personal site
│   └── caddy-sablier/           #   Caddy + Sablier plugin image
├── stacks/                      # Docker Compose files — one per host
│   ├── optiplex.yml             #   optiplex services (caddy, jellyfin, immich, …)
│   ├── rpi3.yml                 #   pihole + monitoring trio
│   ├── openclaw.yml             #   openclaw agent + monitoring trio
│   ├── localllm.yml             #   ollama + open-webui + monitoring trio
│   └── homepage/                #   Homepage dashboard config templates (rendered to optiplex)
├── playbooks/
│   ├── site.yml                 #   OS-level config (packages, SSH, UFW, Docker, Tailscale)
│   ├── deploy-stacks.yml        #   Templates + deploys stacks/<host>.yml; configures Uptime Kuma
│   └── configure-routeros.yml   #   MikroTik full config via RouterOS API
├── inventory/
│   ├── hosts.yml                #   Host inventory + groups (rpis, x86, routers, services)
│   ├── group_vars/all/
│   │   ├── main.yml             #   dns_records, proxy_services, ondemand_services, packages, …
│   │   └── vault.yml            #   Encrypted secrets (ansible-vault)
│   └── host_vars/               #   Per-host UFW rules, Tailscale flags, MikroTik DHCP leases
├── cloud-init/                  # First-boot autoinstall configs
│   ├── rpi/                     #   user-data + network-config (cloud-init on SD boot partition)
│   ├── optiplex/                #   autoinstall user-data (embedded into ISO)
│   ├── openclaw/                #   autoinstall user-data (WiFi onboarding)
│   └── localllm/                #   autoinstall user-data (wired Ethernet, NVMe, swap)
├── scripts/
│   ├── flash.sh                 #   Download images, list disks, flash to SD/USB
│   └── build-iso.sh             #   Repack Ubuntu ISO with autoinstall (xorriso)
├── docs/                        # Architecture + runbooks (see Documentation below)
├── .github/workflows/           # CI: path-filtered build + push to GHCR
└── Makefile
```

## SSH

Every host uses the same key (`~/.ssh/id_ed25519_gws_homelab`) and user (`gws`). Convenience aliases in `ssh.config`:

```bash
cat ssh.config >> ~/.ssh/config
ssh rpi3 / ssh optiplex / ssh openclaw / ssh localllm
```

## Services

All `https://*.internal/` URLs are served by Caddy on optiplex (uses the internal CA — see [`docs/https-internal-ca.md`](docs/https-internal-ca.md) to trust it on a new device). Tailscale split-DNS routes `*.internal` → Pi-hole on rpi3 from any tailnet device.

Highlights — full list rendered on `https://homepage.internal`:

- **Media**: Jellyfin, Audiobookshelf, Immich, Navidrome, Jellyseerr, MusicSeerr
- **Tools**: Paperless-ngx, Stirling-PDF, IT-Tools, Draw.io, Actual Budget
- **Downloads**: qBittorrent (via Mullvad/gluetun), Radarr/Sonarr/Lidarr, Prowlarr, Bazarr
- **Monitoring**: Beszel (per-host metrics), Dozzle (logs), Uptime Kuma (uptime + Telegram alerts), Pi-hole
- **AI**: Open WebUI (`chat.internal`), Ollama API (`localllm.internal:11434`), OpenClaw (Tailscale-only)
- **Infrastructure**: Caddy, Cloudflare Tunnel, Sablier (scale-to-zero)

## Documentation

| File | What it covers |
|------|----------------|
| [`CLAUDE.md`](CLAUDE.md) | Project conventions and AI-assistant context — architecture, philosophy, container standards |
| [`docs/network-design.md`](docs/network-design.md) | Full VLAN, firewall, DNS, DHCP, WiFi design and rationale |
| [`docs/mikrotik-bootstrap.md`](docs/mikrotik-bootstrap.md) | Bringing a factory-reset MikroTik back to configured state |
| [`docs/tailscale.md`](docs/tailscale.md) | Tailscale ACLs, tags, family access, threat model |
| [`docs/https-internal-ca.md`](docs/https-internal-ca.md) | Trusting the Caddy internal CA on new devices |
| [`docs/runbooks.md`](docs/runbooks.md) | Post-deploy manual steps per service (Jellyfin first-run, Beszel registration, …) |
| [`docs/add-service.md`](docs/add-service.md) | Checklist for adding a new Docker service |
| [`TODO.md`](TODO.md) | Open improvements (security, backups, etc.) |

## Workflow

```
apps/<name>/  →  GitHub Actions (path-filtered)  →  ghcr.io/gmourtz/<name>:tag
                                                         ↓
stacks/<host>.yml  →  ansible-playbook deploy-stacks.yml  →  running on host
```

- Edit code in `apps/` → CI builds + pushes (multi-arch)
- Edit infra in `stacks/`, `inventory/`, `playbooks/` → `make stacks` / `make deploy`
- Edit MikroTik config in `inventory/host_vars/mikrotik.yml` → `make routeros`
