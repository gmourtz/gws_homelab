# air_quality_home

Monitors **Awair Element** air-quality sensors. A collector polls each device's
local HTTP API on a fixed interval and stores readings in SQLite; a Streamlit
dashboard visualises the latest reading and historical trends.

Runs on **optiplex** as two containers built from one image
(`ghcr.io/gmourtz/air_quality_home`):

| Service                 | Role                              | Lifecycle |
|-------------------------|-----------------------------------|-----------|
| `air-quality-collector` | Polls sensors, writes the SQLite DB | always-on |
| `air-quality-dashboard` | Streamlit UI at `air-quality.internal` | Sablier on-demand |

Both share the `air-quality-data` Docker volume (the dashboard mounts it read-only).

## Configuration

All configuration is environment-driven — there is no config file. The values
are set in the homelab Ansible repo, not here:

| Variable | Where it's set | Notes |
|---|---|---|
| `AWAIR_DEVICES` | `awair_devices` in `inventory/group_vars/all/main.yml` | JSON array, templated into the stack |
| `POLL_INTERVAL` | `stacks/optiplex.yml` | Seconds between cycles (default 120) |
| `DATA_DIR` | Dockerfile / stack | SQLite DB location (default `/data`) |
| `HEALTH_PORT` | `stacks/optiplex.yml` | Collector `/health` endpoint port (default 8502) |

`AWAIR_DEVICES` format:

```json
[{"name": "Awair Element 1", "hostname": "192.168.1.100", "device_mac": "70:88:6b:14:ca:f0"}]
```

> **Note:** the sensors currently sit on the legacy ZTE network (`192.168.1.x`),
> whose DHCP is not IaC-managed. If a sensor's IP drifts, update `awair_devices`.
> When they move to the GWS-IoT VLAN, update the IPs to `192.168.20.x`.

## Deploying

Built and scanned automatically by the monorepo CI (`.github/workflows/build-image.yml`)
on any change under `apps/`. Deploy with:

```bash
make deploy   # DNS — picks up air-quality.internal
make stacks   # rolls out the collector + dashboard containers
```

## Monitoring

The collector serves a `/health` endpoint (port 8502) that returns **200 only
while every configured sensor is reporting**, else **503** with a body naming
the stale sensor. **Uptime Kuma** monitors it at `air-quality-collector.internal`
and alerts on failure — this catches a crashed or stuck collector, a DB failure,
or a sensor that has stopped responding. The collector's Docker healthcheck hits
the same endpoint, so `docker ps` / Beszel reflect the true state.

The Sablier on-demand dashboard is intentionally **not** monitored — polling it
would keep it permanently warm and defeat scale-to-zero. Failures are also logged
to stdout, visible in **Dozzle** / **Beszel**.

## Local development

```bash
pip install -r requirements.txt pytest
pytest tests/ -v

# Run against a sensor on your LAN
export AWAIR_DEVICES='[{"name":"Test","hostname":"192.168.1.100"}]'
export DATA_DIR=./data
python src/collect_data.py            # collector
streamlit run src/app.py              # dashboard
```
