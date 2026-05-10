#!/usr/bin/env bash
# ------------------------------------------------------------------
# wifi-capture.sh — passive 802.11 capture wrapper for the Kali lab box (rpi5).
#
# Runs ON the Kali host (not the Ansible controller). Installed by
# playbooks/site-kali.yml at /usr/local/sbin/wifi-capture.sh.
#
# Workflow:
#   sudo wifi-capture.sh scan                          # list nearby APs (30s)
#   sudo wifi-capture.sh start -c 11 -b AA:BB:CC:...   # capture on channel 11
#   sudo wifi-capture.sh status                        # is a capture running?
#   sudo wifi-capture.sh stop                          # stop, restore iface
#   sudo wifi-capture.sh deauth -b <ap> -t <client>    # force handshake re-key
#
# Decryption (in Wireshark):
#   Edit → Preferences → Protocols → IEEE 802.11 → Decryption keys
#     Type: wpa-pwd     Key: <psk>:<ssid>
#   Decryption only works for WPA2-PSK frames where the 4-way handshake is
#   ALSO present in the capture. WPA3 (SAE) is not decryptable this way.
#
# RF requirements:
#   - The host must be in radio range of the target AP.
#   - The radio must support monitor mode. Pi 5 internal WiFi works on Kali
#     via the brcmfmac driver (limited to 2.4 GHz reliably). For 5 GHz,
#     use a USB adapter such as Alfa AWUS036ACM (mt76x2u).
# ------------------------------------------------------------------
set -euo pipefail

DEFAULT_IFACE="${WIFI_IFACE:-wlan0}"
STATE_DIR=/run/wifi-capture
OUT_DIR=/var/lib/wifi-capture

usage() {
    cat <<EOF
Usage: $0 <command> [options]

Commands:
  scan    [-i IFACE] [-d SECONDS]
              Passive scan. Prints BSSIDs, SSIDs, channels, signal.
  start   -c CHANNEL [-b BSSID] [-i IFACE] [-o NAME]
              Start a background capture. Channel is required (find it via 'scan').
              BSSID filter is recommended (cuts noise + ensures handshake capture
              for that AP). Output written to ${OUT_DIR}/<NAME>-NN.cap.
  stop        Stop the running capture and return the iface to managed mode.
  status      Show whether a capture is running and where output is.
  deauth  -b BSSID -t CLIENT_MAC
              Send 5 deauth frames to force CLIENT to re-handshake with BSSID.
              Only useful if a capture is already running on the right channel.

Defaults:
  IFACE   ${DEFAULT_IFACE}        (override with -i or env WIFI_IFACE)
  NAME    capture-YYYYmmdd-HHMMSS
EOF
    exit 1
}

require_root() {
    [[ $EUID -eq 0 ]] || { echo "ERROR: must run as root (use sudo)"; exit 1; }
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: '$1' not installed. Install via: apt install $2"
        exit 1
    }
}

ensure_state() {
    install -d -m 0750 "$STATE_DIR"
    install -d -m 0755 "$OUT_DIR"
}

# Returns the monitor-mode iface name created by airmon-ng for $1.
# brcmfmac (Pi internal): often keeps the same name and just flips type.
# mt76/rtl8812: typically creates <iface>mon.
detect_mon_iface() {
    local base="$1"
    if iw dev "${base}mon" info >/dev/null 2>&1; then
        echo "${base}mon"
    elif iw dev "$base" info 2>/dev/null | grep -q "type monitor"; then
        echo "$base"
    else
        return 1
    fi
}

enable_monitor() {
    local iface="$1"
    echo "==> Killing processes that grab the radio (NetworkManager, wpa_supplicant)..."
    airmon-ng check kill >/dev/null
    echo "==> Enabling monitor mode on ${iface}..."
    airmon-ng start "$iface" >/dev/null
    local mon
    mon=$(detect_mon_iface "$iface") || {
        echo "ERROR: could not detect monitor iface for $iface"
        exit 1
    }
    echo "==> Monitor iface: $mon"
    echo "$mon"
}

disable_monitor() {
    local mon="$1"
    echo "==> Disabling monitor mode on ${mon}..."
    airmon-ng stop "$mon" >/dev/null || true
    # Best-effort: restart NetworkManager so normal WiFi returns.
    if systemctl list-unit-files NetworkManager.service >/dev/null 2>&1; then
        systemctl restart NetworkManager || true
    fi
}

cmd_scan() {
    local iface="$DEFAULT_IFACE" duration=30
    while getopts "i:d:" opt; do
        case "$opt" in
            i) iface="$OPTARG" ;;
            d) duration="$OPTARG" ;;
            *) usage ;;
        esac
    done
    require_root
    require_cmd airmon-ng aircrack-ng
    require_cmd airodump-ng aircrack-ng
    local mon
    mon=$(enable_monitor "$iface" | tail -n1)
    echo "==> Scanning for ${duration}s. Press Ctrl-C early to stop."
    set +e
    timeout "$duration" airodump-ng "$mon"
    set -e
    disable_monitor "$mon"
}

cmd_start() {
    local iface="$DEFAULT_IFACE" channel="" bssid="" name=""
    while getopts "i:c:b:o:" opt; do
        case "$opt" in
            i) iface="$OPTARG" ;;
            c) channel="$OPTARG" ;;
            b) bssid="$OPTARG" ;;
            o) name="$OPTARG" ;;
            *) usage ;;
        esac
    done
    [[ -n "$channel" ]] || { echo "ERROR: -c CHANNEL is required"; usage; }
    [[ -z "$name" ]] && name="capture-$(date +%Y%m%d-%H%M%S)"
    require_root
    require_cmd airmon-ng aircrack-ng
    require_cmd airodump-ng aircrack-ng
    ensure_state

    if [[ -f "$STATE_DIR/pid" ]] && kill -0 "$(cat "$STATE_DIR/pid")" 2>/dev/null; then
        echo "ERROR: a capture is already running (pid $(cat "$STATE_DIR/pid")). Run '$0 stop' first."
        exit 1
    fi

    local mon
    mon=$(enable_monitor "$iface" | tail -n1)

    local prefix="${OUT_DIR}/${name}"
    echo "==> Starting airodump-ng on ${mon} channel ${channel}${bssid:+ bssid ${bssid}}"
    nohup airodump-ng \
        --channel "$channel" \
        ${bssid:+--bssid "$bssid"} \
        --write "$prefix" \
        --output-format pcap \
        "$mon" \
        >"$STATE_DIR/log" 2>&1 &
    echo $! > "$STATE_DIR/pid"
    echo "$mon" > "$STATE_DIR/mon"
    echo "$prefix" > "$STATE_DIR/prefix"

    sleep 1
    if ! kill -0 "$(cat "$STATE_DIR/pid")" 2>/dev/null; then
        echo "ERROR: airodump-ng failed to start. Last log:"
        tail -n 20 "$STATE_DIR/log"
        disable_monitor "$mon"
        rm -f "$STATE_DIR"/{pid,mon,prefix}
        exit 1
    fi

    cat <<EOF
==> Capture running.
    pid:    $(cat "$STATE_DIR/pid")
    iface:  $mon
    output: ${prefix}-01.cap
    log:    $STATE_DIR/log

Stop with: sudo $0 stop
EOF
}

cmd_stop() {
    require_root
    [[ -f "$STATE_DIR/pid" ]] || { echo "no capture running."; exit 0; }
    local pid mon
    pid=$(cat "$STATE_DIR/pid")
    mon=$(cat "$STATE_DIR/mon")
    echo "==> Stopping capture (pid ${pid})..."
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    disable_monitor "$mon"
    local prefix
    prefix=$(cat "$STATE_DIR/prefix")
    rm -f "$STATE_DIR"/{pid,mon,prefix}
    echo "==> Done. Capture file(s):"
    ls -lh "${prefix}"*.cap 2>/dev/null || echo "   (no .cap file produced — was the radio in range?)"
}

cmd_status() {
    if [[ -f "$STATE_DIR/pid" ]] && kill -0 "$(cat "$STATE_DIR/pid")" 2>/dev/null; then
        echo "running"
        echo "  pid:    $(cat "$STATE_DIR/pid")"
        echo "  iface:  $(cat "$STATE_DIR/mon")"
        echo "  prefix: $(cat "$STATE_DIR/prefix")"
        ls -lh "$(cat "$STATE_DIR/prefix")"*.cap 2>/dev/null || true
    else
        echo "not running"
        rm -f "$STATE_DIR"/{pid,mon,prefix} 2>/dev/null || true
    fi
}

cmd_deauth() {
    local bssid="" client=""
    while getopts "b:t:" opt; do
        case "$opt" in
            b) bssid="$OPTARG" ;;
            t) client="$OPTARG" ;;
            *) usage ;;
        esac
    done
    [[ -n "$bssid" && -n "$client" ]] || { echo "ERROR: -b BSSID and -t CLIENT are required"; usage; }
    require_root
    require_cmd aireplay-ng aircrack-ng
    [[ -f "$STATE_DIR/mon" ]] || { echo "ERROR: no capture running. Start one first so airodump is on the right channel."; exit 1; }
    local mon
    mon=$(cat "$STATE_DIR/mon")
    echo "==> Sending 5 deauth frames to ${client} via ${bssid} on ${mon}..."
    aireplay-ng --deauth 5 -a "$bssid" -c "$client" "$mon"
}

[[ $# -ge 1 ]] || usage
sub="$1"; shift
case "$sub" in
    scan)   cmd_scan   "$@" ;;
    start)  cmd_start  "$@" ;;
    stop)   cmd_stop   "$@" ;;
    status) cmd_status "$@" ;;
    deauth) cmd_deauth "$@" ;;
    *)      usage ;;
esac
