# Homelab TODO

## Pi-hole — Enable DNSSEC

DNSSEC prevents DNS spoofing (forged responses). Pi-hole supports it natively.
Add to `stacks/rpi3.yml` under pihole environment:
```yaml
FTLCONF_dns_dnssec: "true"
```
Then redeploy: `ansible-playbook playbooks/deploy-stacks.yml --limit rpi3`

## openclaw — Physical security hardening

The laptop holds sensitive credentials (Anthropic API key, messaging channel tokens).
Currently no protection against physical access attacks (recovery mode → root shell).

### Layer 1 — GRUB password
Prevents booting into recovery mode without a password.
- Set via `grub-mkpasswd-pbkdf2` + `/etc/grub.d/40_custom` + `update-grub`
- Can be done live on the running host (no reflash needed)

### Layer 2 — BIOS/UEFI password + disable USB boot
Prevents booting from external media entirely.
- Enter BIOS via F2 on Lenovo
- Set Supervisor password
- Set boot order: internal SSD only, disable USB boot
- Can be done live (no reflash needed)

### Layer 3 — Full disk encryption (LUKS + TPM2 auto-unlock)
Gold standard. SSD is unreadable if removed.
TPM2 auto-unlock means the laptop decrypts at boot automatically,
but only if hardware hasn't been tampered with — no manual passphrase needed.
- Requires updating `cloud-init/openclaw/user-data` autoinstall storage config
- Requires rebuilding ISO + reflashing + reinstalling
- Claude Code can implement this when ready
