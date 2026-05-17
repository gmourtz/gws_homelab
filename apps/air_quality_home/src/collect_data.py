"""Awair Element air-quality collector.

Polls one or more Awair Element devices over the local network, stores each
reading in SQLite, and runs forever on a fixed interval. Failures are logged to
stdout (surfaced via Dozzle / Beszel).

A small HTTP health endpoint is served so Uptime Kuma can monitor that the
collector is actually working — not just that the process is alive. It reports
200 only when every configured sensor has produced a reading recently.

Configuration is entirely via environment variables — see the homelab stack
`stacks/optiplex.yml` and `inventory/group_vars/all/main.yml`:

    AWAIR_DEVICES   JSON array: [{"name","hostname","device_mac"}, ...]
    POLL_INTERVAL   seconds between collection cycles (default 120)
    DATA_DIR        directory for the SQLite DB (default /data)
    HEALTH_PORT     port for the /health endpoint (default 8502)

This module is import-safe: importing it has no side effects beyond configuring
logging, so the pure helpers can be unit-tested directly.
"""

import datetime
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Logging — stdout only; Docker captures it (Dozzle / Beszel read from there) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

# Local timezone — handles BST/GMT automatically
LOCAL_TIMEZONE = pytz.timezone("Europe/London")

# Health state: device name -> datetime of its last successfully saved reading.
# Written by the collection loop, read by the health-endpoint thread.
LAST_SUCCESS = {}
STARTED_AT = datetime.datetime.now()


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def get_db_path():
    """Absolute path to the SQLite database (under DATA_DIR)."""
    return os.path.join(os.getenv("DATA_DIR", "/data"), "awair_data.db")


def parse_devices(raw):
    """Parse the AWAIR_DEVICES JSON string into a validated list of dicts.

    Raises json.JSONDecodeError on bad JSON and ValueError on a structurally
    invalid payload (not a list, or an entry missing name/hostname).
    """
    devices = json.loads(raw)
    if not isinstance(devices, list):
        raise ValueError("AWAIR_DEVICES must be a JSON array")
    for entry in devices:
        if not isinstance(entry, dict) or "name" not in entry or "hostname" not in entry:
            raise ValueError(
                f"each AWAIR_DEVICES entry needs 'name' and 'hostname': {entry!r}"
            )
    return devices


def load_devices():
    """Read and validate AWAIR_DEVICES from the environment.

    Exits the process (sys.exit(1)) if the variable is missing, malformed, or
    empty — there is nothing useful for the collector to do without devices.
    """
    raw = os.getenv("AWAIR_DEVICES", "[]")
    try:
        devices = parse_devices(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"Error parsing AWAIR_DEVICES: {e}")
        sys.exit(1)
    if not devices:
        logging.error("No Awair devices configured. Set AWAIR_DEVICES. Exiting.")
        sys.exit(1)
    return devices


# --------------------------------------------------------------------------- #
# Health endpoint
# --------------------------------------------------------------------------- #
def health_threshold(poll_interval):
    """Seconds a device may go without a reading before it's 'stale'.

    Three poll cycles of grace (a real outage, not a one-off blip), with a
    300 s floor so very short intervals still tolerate a transient miss.
    """
    return max(poll_interval * 3, 300)


def compute_health(devices, last_success, started_at, now, threshold_seconds):
    """Decide whether the collector is healthy. Returns (is_healthy, report).

    A device is healthy when it produced a reading within threshold_seconds.
    Before its first reading, a startup grace period (one threshold window from
    process start) keeps it healthy so a freshly started container doesn't flap.
    """
    lines = []
    healthy = True
    for device in devices:
        name = device["name"]
        last = last_success.get(name)
        if last is not None:
            age = (now - last).total_seconds()
            ok = age < threshold_seconds
            lines.append(
                f"{name}: {'ok' if ok else 'STALE'} — last reading {age:.0f}s ago"
            )
        else:
            age = (now - started_at).total_seconds()
            ok = age < threshold_seconds
            lines.append(
                f"{name}: {'starting' if ok else 'STALE'} — no reading yet "
                f"({age:.0f}s since start)"
            )
        healthy = healthy and ok
    return healthy, "\n".join(lines)


def start_health_server(devices, poll_interval, port):
    """Serve the /health endpoint on a daemon thread. Returns the HTTPServer."""
    threshold = health_threshold(poll_interval)

    class HealthHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            healthy, report = compute_health(
                devices, LAST_SUCCESS, STARTED_AT, datetime.datetime.now(), threshold
            )
            body = (("OK\n" if healthy else "UNHEALTHY\n") + report + "\n").encode()
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # silence per-request access logging

    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info(f"Health endpoint listening on :{port}")
    return server


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def init_db(db_path):
    """Open the SQLite database, ensuring the table and timestamp index exist."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS air_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT,
            device_mac TEXT,
            timestamp TEXT,
            score REAL,
            dew_point REAL,
            temp REAL,
            humid REAL,
            abs_humid REAL,
            co2 REAL,
            co2_est REAL,
            voc REAL,
            pm25 REAL,
            pm10_est REAL
        );
        """
    )
    # The dashboard filters by timestamp on every load — index keeps it fast
    # as years of history accumulate.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_air_quality_ts ON air_quality(timestamp);"
    )
    conn.commit()
    return conn


def save_data_to_db(db_conn, device_name, device_mac, data):
    """Insert one Awair reading into the air_quality table. Returns True on success."""
    sql = """
    INSERT INTO air_quality (
        device_name, device_mac, timestamp, score, dew_point, temp,
        humid, abs_humid, co2, co2_est, voc, pm25, pm10_est
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    # Parse and convert the device timestamp to local time. The Awair API
    # returns ISO-8601 UTC ("...Z"); fall back to "now" if it is missing.
    awair_timestamp_str = data.get("timestamp")
    if awair_timestamp_str:
        utc_dt = datetime.datetime.fromisoformat(
            awair_timestamp_str.replace("Z", "+00:00")
        )
    else:
        utc_dt = datetime.datetime.now(datetime.timezone.utc)
    local_dt = utc_dt.astimezone(LOCAL_TIMEZONE)
    timestamp_to_save = local_dt.isoformat(timespec="milliseconds")

    record = (
        device_name,
        device_mac,
        timestamp_to_save,
        data.get("score"),
        data.get("dew_point"),
        data.get("temp"),
        data.get("humid"),
        data.get("abs_humid"),
        data.get("co2"),
        data.get("co2_est"),
        data.get("voc"),
        data.get("pm25"),
        data.get("pm10_est"),
    )
    try:
        cur = db_conn.cursor()
        cur.execute(sql, record)
        db_conn.commit()
        logging.info(f"[OK] saved {device_name} ({device_mac}) @ {timestamp_to_save}")
        return True
    except sqlite3.DatabaseError as e:
        logging.error(f"[DB ERROR] could not insert for {device_name}: {e}")
        db_conn.rollback()
        return False


# --------------------------------------------------------------------------- #
# Awair device HTTP API
# --------------------------------------------------------------------------- #
def build_session():
    """A requests session with retry/backoff on transient HTTP failures."""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("http://", HTTPAdapter(max_retries=retry_strategy))
    return session


def get_awair_data(session, hostname):
    """Fetch the latest air-quality reading from an Awair device."""
    url = f"http://{hostname}/air-data/latest"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logging.error(f"[ERROR] fetching air data from {hostname}: {e}")
        return None


def get_device_config(session, hostname):
    """Fetch device config (including device_uuid) from an Awair device."""
    url = f"http://{hostname}/settings/config/data"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logging.error(f"[ERROR] fetching config data from {hostname}: {e}")
        return None


def resolve_mac(session, device):
    """Return a device's MAC: prefer the configured value, else discover it."""
    mac = device.get("device_mac")
    if mac:
        return mac
    cfg = get_device_config(session, device["hostname"])
    mac = cfg.get("device_uuid") if cfg else None
    if not mac:
        logging.warning(
            f"  ! no MAC for {device['name']} ({device['hostname']}); skipping"
        )
    return mac


# --------------------------------------------------------------------------- #
# Collection loop
# --------------------------------------------------------------------------- #
def collect_cycle(session, db_path, devices):
    """Run a single collection pass: fetch every device and persist readings.

    Records the time of each successful save in LAST_SUCCESS for the health
    endpoint.
    """
    db_conn = init_db(db_path)
    try:
        for device in devices:
            name = device["name"]
            mac = resolve_mac(session, device)
            if not mac:
                continue
            data = get_awair_data(session, device["hostname"])
            if data and save_data_to_db(db_conn, name, mac, data):
                LAST_SUCCESS[name] = datetime.datetime.now()
            else:
                logging.warning(f"  ! no reading saved for {name} this cycle")
    finally:
        db_conn.close()


def main():
    devices = load_devices()
    poll_interval = int(os.getenv("POLL_INTERVAL", "120"))
    health_port = int(os.getenv("HEALTH_PORT", "8502"))
    db_path = get_db_path()
    session = build_session()

    logging.info(
        f"Awair collector starting — {len(devices)} device(s), "
        f"poll every {poll_interval}s, db at {db_path}"
    )
    start_health_server(devices, poll_interval, health_port)

    while True:
        try:
            collect_cycle(session, db_path, devices)
        except Exception as e:  # noqa: BLE001 — keep the loop alive on any failure
            logging.exception(f"[FATAL] unexpected error in collection cycle: {e}")
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
