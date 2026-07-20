# Health MCP

SQLite-backed [MCP](https://modelcontextprotocol.io) server — the data interface for a personal
running-coach agent. It ingests Apple Health exports into SQLite and exposes typed read/write
tools over Streamable HTTP. No raw SQL is exposed to the model; every query is parameterized and
pre-defined.

## Architecture

```
Apple Health export.xml ──(ingest.py, on the control node)──▶  health.db (SQLite)
                                                                    │
OpenClaw "Health Coach" agent  ──MCP over HTTP, :8100/mcp──▶   server.py (FastMCP)
(Telegram bot, runs on the openclaw host)                       reads / writes health.db
```

- **`src/ingest.py`** parses `export.xml` and rewrites the *managed* tables. Idempotent full-replace
  (an Apple Health export always contains the complete history).
- **`src/server.py`** (FastMCP, Streamable HTTP, port `8100`, path `/mcp`) exposes 16 typed tools.
- **`src/db.py`** opens SQLite in WAL mode and applies `schema.sql` on startup.
- The agent itself lives elsewhere (OpenClaw, on the `openclaw` host); this service is only the data
  layer it calls.

## Data model: managed vs. manual

Two table classes, and the distinction is load-bearing:

- **Managed** (25 tables — `daily_summary`, `workouts`, `sleep`, `hrv`, `resting_heart_rate`,
  `training_zones`, …): sourced from Apple Health, **wholesale-replaced on every ingest**.
- **Manual** (`meals`, `supplements`, `blood_tests`, `alcohol_caffeine`): written live by the agent
  through the write tools, **never touched by ingest**.

This split is why re-syncing Apple Health data never wipes logged meals or bloodwork. Any sync path
**must** preserve the manual tables — see [Deployment](#deployment-gws_homelab).

## Tools

| | |
|---|---|
| **Write** (manual tables) | `log_meal`, `log_alcohol_caffeine`, `log_blood_test`, `upsert_supplement` |
| **Read** | `get_daily_summary`, `get_recent_workouts`, `get_sleep`, `get_hrv`, `get_resting_heart_rate`, `get_weight`, `get_training_zones`, `get_meals`, `get_supplements`, `get_blood_tests`, `get_alcohol_caffeine`, `get_profile` |

## Layout

```
src/server.py            FastMCP server + tool definitions
src/ingest.py            Apple Health export.xml → SQLite
src/db.py                connection (WAL) + schema init
schema.sql               table definitions (managed + manual)
tests/                   pytest suite
health_coach_prompt.md   agent system prompt (templated into stacks/openclaw.json.j2)
Dockerfile               python:3.12-slim, runs as uid 1000, DB on /data volume
apple_health_export/     ingest input — gitignored (~1.2 GB, re-exported from the Health app)
data/                    SQLite DB — gitignored
```

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest          # 29 tests
```

## Ingesting data

```sh
.venv/bin/python src/ingest.py apple_health_export/export.xml --db data/health.db
```

`--db` defaults to `/data/health.db` (the container volume mount). Run against an existing DB and the
manual tables are preserved; only managed tables are rebuilt.

## Running the server locally

```sh
HEALTH_DB_PATH=./data/health.db .venv/bin/python src/server.py   # serves http://localhost:8100/mcp
```

## Deployment (gws_homelab)

- Image `ghcr.io/gmourtz/health_mcp:latest` (see `Dockerfile`) runs on **optiplex**, DB on the named
  volume `health-data:/data`. Gated behind `openclaw_health_mcp_enabled`.
- The OpenClaw agent reaches it at `http://optiplex.internal:8100/mcp` (scoped MikroTik firewall
  rule). The coach system prompt is `health_coach_prompt.md`, pulled into `stacks/openclaw.json.j2`
  at template time — edit the markdown, then `make stacks`.
- **Data sync — `make health-sync`** (from the repo root) runs `playbooks/sync-health-db.yml`: stop
  the container → pull the live DB → run `ingest.py` **on the control node** against it (preserving
  manual tables) → push it back → restart. Ingest runs on the control node, not the 6 GB optiplex, to
  keep the 1.2 GB XML parse off the constrained box; only the ~3 MB DB crosses the network.

## Operational notes

- **Go-live gap:** `openclaw_health_mcp_enabled` is currently set only in
  `inventory/host_vars/openclaw.yml`, so the optiplex stack won't render the container. Promote it to
  `group_vars/all/` before deploying.
- **No `get_wrist_temperature` tool yet** — the data is ingested (`wrist_temperature`) but not exposed;
  the coach's wrist-temperature illness heuristic is dormant until a read tool is added.
- **Privacy:** the agent's vision model runs on OpenAI, so any food or lab-report images it processes
  leave the homelab.
