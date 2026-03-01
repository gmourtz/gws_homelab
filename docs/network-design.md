# GWS Homelab — Network Design

> Last updated: 2026-02-27 — **implemented and operational**

---

## Table of Contents

- [Overview](#overview)
- [Why This Architecture](#why-this-architecture)
- [Physical Topology](#physical-topology)
- [VLAN Fundamentals](#vlan-fundamentals)
- [VLAN Design](#vlan-design)
- [IP Addressing](#ip-addressing)
- [Bridge and VLAN Filtering](#bridge-and-vlan-filtering)
- [WiFi Architecture](#wifi-architecture)
- [Firewall Design](#firewall-design)
- [DNS and DHCP](#dns-and-dhcp)
- [NAT and Internet Access](#nat-and-internet-access)
- [Service Hardening](#service-hardening)
- [Ansible Automation](#ansible-automation)
- [Bootstrap Procedure](#bootstrap-procedure)
- [Adding a New Device](#adding-a-new-device)
- [Troubleshooting](#troubleshooting)
- [Design Decisions Log](#design-decisions-log)

---

## Overview

The homelab network uses a **MikroTik hAP ac²** as the central router, firewall, DHCP server, and WiFi access point. It sits behind the ISP-provided **ZTE ZXHN H298A** gateway in a double-NAT topology. All LAN traffic passes through the MikroTik, which enforces VLAN segmentation to isolate trusted servers, IoT devices, and guest clients from each other.

The entire MikroTik configuration is managed as Infrastructure as Code (IaC) via an Ansible playbook (`playbooks/configure-routeros.yml`) and variables in `inventory/host_vars/mikrotik.yml`. No manual changes should be made to the router — run `make routeros` to apply any config changes.

---

## Why This Architecture

### Double-NAT behind the ZTE

The ISP gateway (ZTE) cannot be replaced or bridged — it's ISP-managed hardware. The MikroTik sits behind it:

```
Internet -> ZTE (192.168.1.1) -> MikroTik ether1 (DHCP) -> VLANs
```

**Double-NAT is acceptable because:**
- No inbound port forwarding is needed — Tailscale handles all remote access via outbound connections and works through double-NAT (using DERP relay when direct connection fails)
- Performance impact is negligible — one extra NAT hop adds microseconds
- The ZTE's WiFi is disabled — all wireless traffic goes through MikroTik SSIDs

### VLAN segmentation over flat network

A flat network (everything on one subnet) means a compromised IoT device can reach every server. VLANs provide network-level isolation:
- IoT devices can reach the internet but not the trusted servers
- Guest devices are completely isolated
- The trusted VLAN has full access for management

This is the #1 security improvement for a homelab with IoT devices.

### Single router over separate router + switch + AP

The hAP ac² combines router, managed switch (5 GbE ports), and dual-band WiFi AP in one device. For a 2–3 server homelab, a separate switch is unnecessary — ether2–5 provide 4 LAN ports, and built-in WiFi supports multiple SSIDs with per-SSID VLAN tagging. Fewer devices = fewer failure points, less power draw, simpler management.

### No orchestrator (no Swarm, no K3s)

Two nodes with different architectures (ARM vs x86), one with only 1 GB RAM. An orchestrator would consume more resources than it provides value. Every byte of RAM goes to workloads, not control planes.

---

## Physical Topology

```
Hyperoptic ONT (fibre)
│
│  Ethernet
▼
┌──────────────────────────────────┐
│     ZTE ZXHN H298A               │
│     192.168.1.1                  │
│     WiFi: DISABLED               │
│     Role: WAN gateway only       │
└──────────┬───────────────────────┘
           │
           │  ZTE LAN port -> MikroTik ether1
           │  MikroTik gets 192.168.1.x via DHCP
           ▼
┌──────────────────────────────────┐
│     MikroTik hAP ac²             │
│     RouterOS 7.x                 │
│                                  │
│  ether1 ── WAN (DHCP from ZTE)   │
│  ether2 ── rpi3                  │
│  ether3 ── optiplex              │
│  ether4 ── spare                 │
│  ether5 ── spare                 │
│  wifi1  ── 2.4 GHz (3 SSIDs)    │
│  wifi2  ── 5 GHz (2 SSIDs)      │
└──────────────────────────────────┘
```

| Port | Connected Device | VLAN | Notes |
|------|-----------------|------|-------|
| ether1 | ZTE LAN port | N/A (WAN) | DHCP client, default route |
| ether2 | rpi3 | 10 (untagged) | Pi-hole, Beszel agent |
| ether3 | optiplex | 10 (untagged) | Docker workloads |
| ether4 | spare | 10 (untagged) | Available |
| ether5 | spare | 10 (untagged) | Available |
| wifi1 | 2.4 GHz clients | Tagged trunk | GWS-Home (10), GWS-IoT (20), GWS-Guest (30) |
| wifi2 | 5 GHz clients | Tagged trunk | GWS-Home (10), GWS-Guest (30) |

> **Port limit note**: With 4 LAN ports (ether2–5) and 3 servers planned, 1 spare remains. If more wired devices are needed, add a managed switch on ether5 configured as a trunk port.

---

## VLAN Fundamentals

### What is a VLAN?

A VLAN (Virtual LAN) logically divides a single physical network into multiple isolated broadcast domains. Devices on different VLANs cannot communicate directly — traffic between VLANs must be routed through a router that can enforce firewall rules.

### How VLANs work on Ethernet

Each VLAN is identified by a numeric ID (1–4094). Ethernet frames carry a 4-byte **802.1Q tag** that contains the VLAN ID. Two types of ports exist:

- **Access port** (untagged): The switch strips the VLAN tag when sending to the device and adds it when receiving. The device doesn't know it's on a VLAN — it sends normal Ethernet frames. Configured via PVID (Port VLAN ID).
- **Trunk port** (tagged): Carries frames from multiple VLANs with their 802.1Q tags intact. Used between switches, routers, and WiFi APs that need to handle traffic for multiple VLANs.

### How VLANs work on MikroTik (RouterOS 7)

RouterOS uses a **bridge with VLAN filtering** — the recommended approach since RouterOS 7:

1. A single **bridge** (`bridge1`) groups all LAN interfaces
2. **Bridge ports** define how each interface handles VLAN tags:
   - `pvid=10` + `frame-types=admit-only-untagged-and-priority-tagged` → access port on VLAN 10
   - `frame-types=admit-only-vlan-tagged` → trunk port
3. The **bridge VLAN table** declares which VLANs are allowed on which ports (tagged/untagged)
4. **VLAN interfaces** (`bridge1.10`, `bridge1.20`, `bridge1.30`) are created on top of the bridge for Layer 3 (IP addresses, DHCP, routing)

---

## VLAN Design

Three VLANs. No separate management VLAN — single admin, not worth the overhead; router management is accessible only from VLAN 10 via firewall rules.

| VLAN ID | Name | Subnet | Purpose |
|---------|------|--------|---------|
| **10** | Trusted | 192.168.10.0/24 | Servers, workstations, personal devices |
| **20** | IoT | 192.168.20.0/24 | openclaw AI agent, IoT devices — isolated, limited internet |
| **30** | Guest | 192.168.30.0/24 | Guest WiFi — internet only, no LAN access |

### Why three VLANs (not more, not fewer)

- **Fewer (flat network)**: No isolation — a compromised camera can reach your servers
- **More (management VLAN, DMZ, etc.)**: Premature complexity for a one-person homelab. Each VLAN adds firewall rules, DHCP config, and mental overhead. Three covers the real threat model: untrusted IoT, untrusted guests, trusted everything else
- **No management VLAN**: The admin (you) is always on the trusted VLAN. A separate management VLAN adds complexity for zero security benefit when there's one admin

---

## IP Addressing

### VLAN 10 — Trusted (192.168.10.0/24)

| Address | Host | Assignment |
|---------|------|------------|
| 192.168.10.1 | MikroTik gateway | Static (router) |
| 192.168.10.10 | rpi3 | DHCP static lease (MAC: B8:27:EB:7C:73:DB) |
| 192.168.10.20 | optiplex | DHCP static lease (MAC: 48:4D:7E:EA:8A:E1) |
| 192.168.10.30 | (available) | — |
| 192.168.10.100–199 | Dynamic clients | DHCP pool |

### VLAN 20 — IoT (192.168.20.0/24)

| Address | Host | Assignment |
|---------|------|------------|
| 192.168.20.1 | MikroTik gateway | Static (router) |
| 192.168.20.30 | openclaw | DHCP static lease (MAC: 94:E6:F7:E5:2E:32, WiFi) |
| 192.168.20.100–199 | IoT devices | DHCP pool |

### VLAN 30 — Guest (192.168.30.0/24)

| Address | Host | Assignment |
|---------|------|------------|
| 192.168.30.1 | MikroTik gateway | Static (router) |
| 192.168.30.100–199 | Guest devices | DHCP pool |

### Why DHCP static leases instead of OS-level static IPs

Servers use DHCP with MAC-based static leases on the router rather than static IPs configured in each server's netplan. Benefits:
- **Single source of truth**: IP assignments live in `inventory/host_vars/mikrotik.yml`, not scattered across OS configs
- **Easier re-IP**: Change one variable in Ansible, re-run — no need to SSH into each host
- **OS portability**: Reflash a server and it gets the same IP automatically

---

## Bridge and VLAN Filtering

### Bridge configuration

All LAN interfaces (ether2–5, wifi1, wifi2) are members of a single bridge (`bridge1`) with VLAN filtering enabled. This means:

- **Untagged traffic** on ether2–5 is tagged with PVID 10 (trusted) by the bridge
- **Tagged traffic** from WiFi virtual APs carries its VLAN tag through the bridge
- The bridge VLAN table controls which VLANs are permitted on which ports
- Traffic between VLANs is routed through the bridge's VLAN interfaces and subject to firewall rules

### Bridge VLAN table

| VLAN | Untagged (access) ports | Tagged (trunk) ports |
|------|------------------------|---------------------|
| 10 | ether2, ether3, ether4, ether5 | bridge1, wifi1, wifi2 |
| 20 | — | bridge1, wifi1 |
| 30 | — | bridge1, wifi1, wifi2 |

`bridge1` itself is tagged on all VLANs — this is required for the router to process traffic on its VLAN interfaces (bridge1.10, bridge1.20, bridge1.30).

### VLAN filtering safety

Enabling VLAN filtering on a bridge is a **disruptive operation** — it immediately changes how frames are forwarded. On the first run from factory defaults (192.168.88.1), this causes the management connection to drop because the bootstrap IP lives on the bridge master (not a VLAN interface). The playbook handles this by:

1. Creating the bridge with `vlan-filtering: false`
2. Configuring all ports, VLAN table, VLAN interfaces, IPs, firewall, and services
3. Enabling VLAN filtering as the **absolute last task** with `ignore_errors` for the bootstrap run
4. A second run from 192.168.10.1 cleans up bootstrap artifacts

---

## WiFi Architecture

### RouterOS 7 WiFi model

RouterOS 7 uses the `wifi` package (not the legacy `wireless` package). WiFi configuration uses four building blocks:

1. **Security profile** (`sec-<ssid>`) — authentication type + passphrase
2. **Configuration profile** (`cfg-<ssid>`) — SSID name
3. **Datapath profile** (`dp-<ssid>`) — bridge membership + VLAN ID
4. **Virtual AP** (`wifi1-<ssid>`, `wifi2-<ssid>`) — ties a physical radio to a security + config + datapath profile

### Physical radios vs virtual APs

`wifi1` (2.4 GHz) and `wifi2` (5 GHz) are **physical radio interfaces**. They don't carry client traffic directly. Instead, **virtual APs** are created on top of them:

```
Client joins "GWS-Home" on 5 GHz
    |
    v
wifi2-gws-home (virtual AP)
    |
    v
datapath "dp-gws-home" tags frames with vlan-id=10
    |
    v
bridge1 (receives VLAN 10 tagged traffic)
    |
    v
bridge1.10 (VLAN 10 interface, 192.168.10.0/24)
```

The physical radios are bridge ports with `frame-types=admit-only-vlan-tagged` — they only pass tagged frames from their virtual APs. Their `pvid=1` (default) is unused.

### SSIDs

| SSID | Band | VLAN | Security |
|------|------|------|----------|
| GWS-Home | 2.4 + 5 GHz | 10 (Trusted) | WPA2-PSK |
| GWS-IoT | 2.4 GHz only | 20 (IoT) | WPA2-PSK |
| GWS-Guest | 2.4 + 5 GHz | 30 (Guest) | WPA2-PSK |

GWS-IoT is 2.4 GHz only because IoT devices (camera, light strip) only support 2.4 GHz. No point wasting 5 GHz airtime on a network with no 5 GHz clients.

### WiFi passphrases

Stored in Ansible Vault (`inventory/group_vars/all/vault.yml`). The playbook references them via `vars[ssid.vault_passphrase_var]`. **Important**: RouterOS API only supports ASCII characters — no Unicode in passphrases.

---

## Firewall Design

The MikroTik firewall uses connection tracking. `ESTABLISHED/RELATED` is accepted first in every chain, so return traffic flows automatically. Only new connections need explicit rules.

### Input chain (traffic destined for the router itself)

| # | Action | Match | Purpose |
|---|--------|-------|---------|
| 1 | ACCEPT | connection-state=established,related | Return traffic |
| 2 | DROP | connection-state=invalid | Malformed packets |
| 3 | ACCEPT | protocol=icmp, in-interface != ether1 | Ping from LAN |
| 4 | ACCEPT | src=192.168.10.0/24, in-interface=bridge1.10 | Management from trusted |
| 5 | DROP | (everything else) | Default deny |

**Why restrict to VLAN 10**: Only trusted clients can access the router's web UI, SSH, API, and Winbox. IoT and guest devices cannot manage the router.

### Forward chain (traffic routed through the router)

| # | Action | Match | Purpose |
|---|--------|-------|---------|
| 1 | ACCEPT | connection-state=established,related | Return traffic |
| 2 | DROP | connection-state=invalid | Malformed packets |
| 3 | DROP | trusted -> guest | No reason to allow |
| 4 | ACCEPT | trusted -> any | Full outbound + IoT management |
| 5 | ACCEPT | IoT -> Pi-hole:53 (UDP) | DNS resolution via Pi-hole |
| 6 | ACCEPT | IoT -> Pi-hole:53 (TCP) | DNS resolution via Pi-hole (TCP) |
| 7 | ACCEPT | 192.168.20.30 -> optiplex:8090 (TCP) | openclaw -> beszel-hub (monitoring) |
| 8 | ACCEPT | IoT -> internet:80,443 | Device cloud connectivity |
| 9 | ACCEPT | IoT -> internet:123/UDP | NTP for TLS certificate validation |
| 10 | ACCEPT | guest -> internet (via ether1) | Full outbound for guests |
| 11 | DROP | (everything else) | Default deny |

### Inter-VLAN policy summary

| From \ To | Trusted | IoT | Guest | Internet |
|-----------|---------|-----|-------|----------|
| **Trusted** | — | ACCEPT | DROP | ACCEPT |
| **IoT** | Pi-hole:53 + openclaw->beszel:8090 | — | DROP | HTTP/HTTPS/NTP only |
| **Guest** | DROP | DROP | — | ACCEPT |

### NAT

Single masquerade rule on ether1 (WAN). All three VLANs share the same WAN IP via source NAT. No port forwarding — Tailscale handles inbound access.

---

## DNS and DHCP

### DNS strategy

| VLAN | DNS server (via DHCP) | Rationale |
|------|-----------------------|-----------|
| Trusted (10) | Pi-hole (192.168.10.10), 1.1.1.1 | Ad blocking + local DNS, with public fallback |
| IoT (20) | Pi-hole (192.168.10.10) | Ad/telemetry blocking for IoT devices |
| Guest (30) | 1.1.1.1, 9.9.9.9 | Public DNS — guests don't get internal DNS |

**Pi-hole** runs on rpi3 (192.168.10.10). It serves DNS for both VLAN 10 and VLAN 20. The MikroTik firewall explicitly allows IoT -> Pi-hole:53, and rpi3's UFW also allows 192.168.20.0/24 on port 53.

**MikroTik's own DNS** (`ip dns`) uses upstream servers 1.1.1.1 and 9.9.9.9 with `allow-remote-requests=false`. The router itself doesn't serve DNS to clients — that's Pi-hole's job. The router DNS is only for its own NTP resolution, package downloads, etc.

### Local domain and DNS records

The homelab uses `.internal` as the local domain suffix (IANA-reserved for private use). The variable `local_domain: internal` is defined in `inventory/group_vars/all/main.yml` and used across all templates.

DNS records are defined once in `inventory/group_vars/all/main.yml` under `dns_records`:

```yaml
local_domain: internal

dns_records:
  - { name: optiplex, ip: "192.168.10.20", aliases: [beszel, jellyfin] }
  - { name: rpi3,     ip: "192.168.10.10", aliases: [pihole] }
  - { name: openclaw, ip: "192.168.20.30" }
```

These propagate to:
- **Pi-hole dnsmasq** (via `stacks/rpi3.yml` template) — resolves both `beszel.internal` and bare `beszel` network-wide. Uses `local=/internal/` to never forward `.internal` queries upstream.
- **/etc/hosts** on all hosts (via `site.yml`) — resolves FQDNs and bare hostnames locally even if Pi-hole is down

**Search domain**: MikroTik DHCP pushes `domain=internal` (DHCP option 15) to trusted and IoT clients. When a device queries `beszel`, the OS appends `.internal` and resolves `beszel.internal` via Pi-hole. Guest VLAN does not receive a search domain.

### DHCP

One DHCP server per VLAN, each bound to its VLAN interface:

| Server | Interface | Pool | Range |
|--------|-----------|------|-------|
| dhcp-trusted | bridge1.10 | pool-trusted | 192.168.10.100–199 |
| dhcp-iot | bridge1.20 | pool-iot | 192.168.20.100–199 |
| dhcp-guest | bridge1.30 | pool-guest | 192.168.30.100–199 |

Static leases bind MAC addresses to fixed IPs for servers (rpi3, optiplex). Devices use DHCP (not OS-level static IPs) so the router is the single source of truth for IP assignments.

---

## NAT and Internet Access

```
Client (192.168.10.x) -> bridge1.10 -> routing -> masquerade -> ether1 -> ZTE (192.168.1.1) -> Internet
```

- **Source NAT (masquerade)** on ether1 rewrites the source IP of outbound packets to the MikroTik's WAN IP (192.168.1.x from ZTE DHCP)
- The ZTE then applies its own NAT to the ISP's public IP — this is the double-NAT
- Return traffic follows the reverse path via connection tracking
- No destination NAT / port forwarding — Tailscale provides inbound access

---

## Service Hardening

### MikroTik services

| Service | Port | State | Access |
|---------|------|-------|--------|
| API | 8728 | Enabled | 192.168.10.0/24 only |
| SSH | 22 | Enabled | 192.168.10.0/24 only |
| Winbox | 8291 | Enabled | 192.168.10.0/24 only |
| Web (HTTP) | 80 | Enabled | 192.168.10.0/24 only |
| FTP | 21 | **Disabled** | — |
| Telnet | 23 | **Disabled** | — |
| API-SSL | 8729 | **Disabled** | — |
| HTTPS | 443 | **Disabled** | — |

### Other hardening

- **Bandwidth server**: Disabled (information leakage)
- **UPnP**: Disabled (automatic port forwarding is a security risk)
- **IPv6**: Not configured (revisit when needed)
- **NTP**: Client enabled (pool.ntp.org); not exposed to WAN
- **RouterOS DNS**: `allow-remote-requests=false` — no open resolver

### Host-level (rpi3, optiplex)

- **UFW** on every host restricts inbound to specific ports from 192.168.10.0/24 (trusted VLAN). The playbook resets UFW and re-applies all rules on every run (reset-and-reapply) to ensure idempotency — stale rules are always cleaned up
- **rpi3 additionally** allows 192.168.20.0/24 on port 53 (IoT DNS to Pi-hole)
- **fail2ban** protects SSH
- **Tailscale** provides secure remote access (runs on each host, not in containers)

---

## Ansible Automation

### Files

| File | Purpose |
|------|---------|
| `playbooks/configure-routeros.yml` | Full router configuration (25 tasks) |
| `inventory/host_vars/mikrotik.yml` | All router variables (VLANs, firewall, DHCP, WiFi, services) |
| `inventory/group_vars/all/vault.yml` | Encrypted secrets (API password, WiFi passphrases) |

### Running

```bash
# Normal run (router already configured)
make routeros
# or: ansible-playbook playbooks/configure-routeros.yml

# First run from factory defaults
ansible-playbook playbooks/configure-routeros.yml -e ansible_host=192.168.88.1
```

### Idempotency

Every task uses `community.routeros.api_modify` to set the **complete desired state** of a RouterOS configuration path. With `handle_absent_entries: remove`, entries not in the Ansible data are deleted from the router. This means:

- Running the playbook twice produces `changed=0` on the second run
- Removing a firewall rule from the variable file removes it from the router
- Adding a VLAN only requires adding it to the `vlans` list in host_vars

### RouterOS API constraints

| Constraint | Solution |
|---|---|
| API only supports ASCII — no Unicode in comments, passphrases, names | Use `->` instead of `→`, ASCII-only passphrases |
| Bridge port removal kills management connection | Don't use `handle_absent_entries: remove` on bridge ports |
| VLAN filtering enable drops bootstrap connection | Enable as last task with `ignore_errors` |
| Physical WiFi radios can't be deleted | Don't use `handle_absent_entries: remove` on `interface wifi` |
| `ensure_order` requires `handle_absent_entries: remove` | Drop `ensure_order` when `remove` isn't safe |
| Factory reset creates bridge named "bridge" with ports attached | Rename to "bridge1" via `api_find_and_modify` before configuring |

---

## Bootstrap Procedure

One-time manual setup after a factory reset. Once done, Ansible owns the configuration.

### Prerequisites

- Laptop with Ethernet adapter
- Ansible + `community.routeros` collection installed (`>= 2.1.0`)
- Vault password (for API password and WiFi passphrases)

### Steps

1. **Factory reset** the MikroTik (hold reset button 5 seconds, or from RouterOS: `/system reset-configuration`)

2. **Connect laptop to ether2**, set laptop to DHCP or static 192.168.88.2/24

3. **SSH in** and set the admin password + enable API:
   ```bash
   ssh-keygen -R 192.168.88.1    # clear stale host key
   ssh admin@192.168.88.1
   ```
   ```
   /user set admin password=<vault_routeros_api_password>
   /ip service enable api
   ```

4. **First Ansible run** (from bootstrap IP):
   ```bash
   ansible-playbook playbooks/configure-routeros.yml -e ansible_host=192.168.88.1
   ```
   - Configures everything: bridge, VLANs, ports, DHCP, firewall, NAT, WiFi, services
   - Last task enables VLAN filtering — connection drops (expected, ignored)
   - Router is fully configured at this point

5. **Switch laptop to new network**: set IP to 192.168.10.x/24 or connect to GWS-Home WiFi

6. **Second Ansible run** (from final IP):
   ```bash
   ansible-playbook playbooks/configure-routeros.yml
   ```
   - Removes bootstrap IP (192.168.88.1/24 on bridge1)
   - Removes temporary bootstrap firewall rule
   - Tightens IP service restrictions to 192.168.10.0/24 only
   - Confirms full idempotency (`changed=0`)

### What the bootstrap run does differently

When `ansible_host == '192.168.88.1'`, the playbook conditionally:
- Adds a temporary IP 192.168.88.1/24 on bridge1 (bootstrap reachability)
- Adds a temporary firewall rule accepting traffic from 192.168.88.0/24 (bootstrap management)
- Widens IP service address restrictions to include 192.168.88.0/24 (bootstrap API access)
- Ignores errors on the VLAN cutover task (connection drops when filtering enables)

All of these are automatically removed on the second run from 192.168.10.1.

---

## Adding a New Device

### New wired server on VLAN 10

1. **Add DHCP static lease** in `inventory/host_vars/mikrotik.yml`:
   ```yaml
   dhcp_static_leases:
     # ... existing entries ...
     - comment: newhost
       mac_address: "XX:XX:XX:XX:XX:XX"
       ip_address: 192.168.10.30
       server: dhcp-trusted
   ```

2. **Add DNS record** in `inventory/group_vars/all/main.yml`:
   ```yaml
   dns_records:
     # ... existing entries ...
     - { name: newhost, ip: "192.168.10.30", aliases: [myservice] }
   ```

3. **Add to inventory** in `inventory/hosts.yml` and create `inventory/host_vars/newhost.yml`

4. **Apply**:
   ```bash
   make routeros       # update router DHCP leases
   make deploy         # update /etc/hosts + Pi-hole on all hosts
   ```

No MikroTik bridge/port changes needed — ether ports are already in VLAN 10.

### New WiFi SSID

1. Add entry to `wifi_ssids` in `inventory/host_vars/mikrotik.yml`
2. Add passphrase variable to vault
3. If new VLAN: add VLAN to `vlans`, VLAN table, DHCP server/pool/network, firewall rules
4. Run `make routeros`

### New IoT device

No config changes needed — IoT devices join GWS-IoT WiFi and get a DHCP address from pool-iot (192.168.20.100–199). If a static lease is needed, add it to `dhcp_static_leases` and run `make routeros`.

---

## Troubleshooting

### Can't reach router after VLAN filtering enabled

The bootstrap IP (192.168.88.1) only exists during the first run. After VLAN filtering, connect to 192.168.10.1 from a trusted VLAN device (wired on ether2–5 or GWS-Home WiFi).

**If completely locked out**: Factory reset (hold reset button 5 seconds) and re-run the [bootstrap procedure](#bootstrap-procedure).

### IoT device can't resolve DNS

Check both layers:
1. **MikroTik firewall**: IoT -> Pi-hole:53 rule exists (`/ip firewall filter print`)
2. **rpi3 UFW**: allows 192.168.20.0/24 on port 53 (`sudo ufw status`)

### Device gets wrong IP or no IP

Check the DHCP static lease MAC address matches the device:
```
/ip dhcp-server lease print
```

Ensure the device's netplan is set to DHCP (not static):
```yaml
# /etc/netplan/50-cloud-init.yaml on the host
network:
  ethernets:
    eth0:
      dhcp4: true
```

### WiFi AP not broadcasting

Check virtual AP is enabled and master-interface is correct:
```
/interface wifi print
```

### Ansible playbook times out on bridge tasks

Never use `handle_absent_entries: remove` on `interface bridge port` or `interface wifi` — see [RouterOS API constraints](#routeros-api-constraints).

---

## Design Decisions Log

| Decision | Rationale |
|---|---|
| Double-NAT behind ZTE | Can't replace ISP gateway; Tailscale works through it; no inbound ports needed |
| 3 VLANs (trusted/IoT/guest) | Covers real threat model without over-engineering; each additional VLAN adds operational overhead |
| No management VLAN | Single admin, always on trusted VLAN; separate management VLAN adds complexity for zero benefit |
| DHCP static leases over OS static IPs | Single source of truth on router; easier re-IP; OS-agnostic |
| Pi-hole for IoT DNS | Ad/telemetry blocking + visibility into IoT device behavior |
| Guest uses public DNS, not Pi-hole | Guests don't need (and shouldn't have) access to internal DNS records |
| UFW kept despite MikroTik firewall | Defense-in-depth; MikroTik handles inter-VLAN, UFW handles intra-VLAN (same-subnet) threats |
| WiFi passphrases in Ansible Vault | IaC principle; reproducible without manual WiFi setup |
| API-only router config (no SSH/CLI) | `community.routeros.api_modify` provides idempotent, declarative state management |
| No `--check` on first bootstrap run | `--check` can't predict bridge rename or VLAN cutover behavior; first run is inherently destructive |
| IoT limited to HTTP/HTTPS/NTP outbound | Minimum viable internet access — cloud control apps work, but no SSH/VPN/arbitrary exfiltration |
| 2.4 GHz only for IoT SSID | IoT devices are 2.4 GHz only; saves 5 GHz airtime for trusted/guest |
