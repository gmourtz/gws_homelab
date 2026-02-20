# Portfolio Agent

Daily portfolio monitor. Fetches positions from Trading 212, computes health metrics, runs IPS policy rules, generates rebalance candidates, asks GPT-4o to explain what matters, sends the report to Telegram.

**Read-only.** The agent never places orders.

## Architecture

```
Trading 212 API         Finnhub API
     │                      │
     ▼                      ▼
┌──────────┐          ┌──────────┐
│ Snapshot │          │ News +   │
│ (account │          │ Fundamntl│
│  + pos.) │          │ + Earns  │
└────┬─────┘          └────┬─────┘
     │                     │
     ▼                     │
┌──────────┐               │
│ metrics  │ deterministic │
│ .py      │ computation   │
└────┬─────┘               │
     │                     │
     ├───────────┐         │
     ▼           ▼         │
┌──────────┐ ┌──────────┐  │
│optimizer │ │ policy   │  │
│ .py      │ │ .py      │  │
│ rebalance│ │ IPS rules│  │
└────┬─────┘ └────┬─────┘  │
     │            │        │
     └──────┬─────┘        │
            ▼              ▼
┌──────────────────────────────┐
│ analyzer.py                  │
│ GPT-4o receives ONLY:        │
│  • health score + sub-scores │
│  • triggered alerts          │
│  • rebalance candidates      │
│  • top movers + news         │
│ Never sees raw API data.     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ nodes.py — message builder   │
│  deterministic header        │
│  + AI narrative              │
└──────────────┬───────────────┘
               │
               ▼
           Telegram
```

All behaviour is driven by an **Investment Policy Statement** (`ips.yml`) — allocation targets, bands, thresholds, and constraints. The IPS is the single source of truth for what the agent considers healthy, breached, or actionable.

## Pipeline

Every cycle (default: 24h) runs a 9-node [LangGraph](https://github.com/langchain-ai/langgraph) pipeline. Node implementations live in `nodes.py` (independently testable); wiring lives in `graph.py`.

1. **Fetch** — `trading212.py` calls `/equity/account/summary` and `/equity/positions` via HTTP Basic Auth. Two API calls total.
2. **Validate** — Schema check, currency normalisation, data-issue flags. Guards against zero-price positions and missing fields.
3. **Store** — `store.py` persists the snapshot as JSONL for time-series history. Deduplicates by date; per-cycle read cache avoids repeated disk I/O.
4. **Compute** — `metrics.py` produces `PortfolioMetrics` + `TimeSeriesMetrics` + per-stock scores: P/L, concentration (HHI, top-N weights), market breakdown, health score (0–100, four sub-scores at 0–25 each), fundamental scores, valuation signals, drawdown, volatility. No LLM involved.
5. **Optimise** — `optimizer.py` generates 2–3 deterministic rebalance candidates (policy rebalance, risk-reduction via HRP, do-nothing). Never places orders.
6. **Evaluate** — `policy.py` checks metrics against IPS thresholds and emits typed `Alert` objects with severity (ACTION / WARNING / INFO). Rules cover: bucket drift (5/25 band rule), position concentration, cash bounds, P/L thresholds, health score, drawdown tolerance, data integrity.
7. **Research** — `news.py` fetches news + fundamentals from Finnhub for flagged tickers. Ticker mapping handled by `tickers.py`.
8. **Analyse** — `analyzer.py` sends only the pre-computed metrics, alerts, and rebalance options to GPT-4o with structured output (Pydantic schema). The LLM explains why alerts matter, recommends a rebalance candidate, identifies cross-position risks. Schema-locked — the model cannot invent fields.
9. **Notify** — `notifier.py` assembles a structured Telegram message: deterministic header (health score, alert count, quick stats) + AI narrative. Auto-splits if >4000 chars. Falls back to plain text if Markdown fails.

## Tech stack

| Layer | Tool | Role |
|---|---|---|
| Runtime | Python 3.12 (slim) | Single long-running process |
| Orchestration | LangGraph | 9-node stateful pipeline with conditional edges |
| Broker API | Trading 212 Public API v0 | Positions + account summary (Basic Auth) |
| Market data | Finnhub free tier | News, fundamentals, company profiles, earnings calendar |
| LLM | OpenAI GPT-4o | Structured narrative analysis only — never decides what to flag |
| IPS | `ips.yml` (frozen dataclass) | Allocation targets, bands, thresholds, constraints |
| Notifications | Telegram Bot API | Message delivery |
| Persistence | JSONL + JSON (Docker volume) | Time-series snapshots + persistent state |
| Container | Docker (GHCR) | `ghcr.io/gmourtz/portfolio_agent:latest` |
| CI | GitHub Actions | Multi-arch build (amd64+arm64), Trivy scan |
| Testing | pytest (131 tests) | Unit tests for all modules |
| Secrets | Ansible Vault → `.env` | Deployed to host by `deploy-stacks.yml` |

## IPS (Investment Policy Statement)

All allocation targets and thresholds are defined in `ips.yml`. The agent reads this on startup and passes it to every component. Example structure:

```yaml
version: 1
base_currency: GBP
buckets:
  - name: US Equity
    target_pct: 50
    markets: [US]
  - name: UK Equity
    target_pct: 20
    markets: [UK]
  # ... more buckets ...
  - name: Cash
    type: cash
    target_pct: 5
```

Each bucket has configurable bands (`band_abs`, `band_rel`) for the 5/25 drift rule.

## Metrics computed (deterministic)

| Metric | What it measures |
|---|---|
| Health score (0–100) | Composite of four sub-scores below |
| Diversification (0–25) | HHI-based, penalises top-1 > 15–20% |
| Risk (0–25) | Fraction of positions in deep loss (>20%) |
| Cash buffer (0–25) | Optimal at 3–10%, penalises <1% or >30% |
| Momentum (0–25) | Win ratio + overall P/L direction |
| HHI | Herfindahl-Hirschman Index (sum of squared weights) |
| Market weights | Geographic breakdown by exchange (via `tickers.py`) |
| Per-stock: fundamental score (0–100) | Growth + profitability + valuation + balance sheet |
| Per-stock: valuation signal | CHEAP / FAIR / EXPENSIVE via PEG-like approach |
| Time-series: drawdown | Max drawdown from peak (requires ≥5 snapshots) |
| Time-series: volatility | Annualised return volatility |
| Time-series: daily returns | Used by HRP optimizer for correlation-aware rebalancing |

## Policy rules (IPS-driven)

| Rule | Default threshold | Severity |
|---|---|---|
| Bucket drift (5/25 rule) | ±5pp absolute or ±25% relative | ACTION |
| Single position > limit | 20% | ACTION |
| Top 3 > limit | 50% | WARNING |
| Cash below minimum | 2% | WARNING |
| Cash above maximum | 25% | INFO |
| Position gain > threshold | 100% | WARNING |
| Position loss > threshold | -30% WARNING, -50% ACTION | ACTION/WARNING |
| Health score < critical | 40 | ACTION |
| Health score < warning | 60 | WARNING |
| Max drawdown > tolerance | configurable | ACTION |
| Zero-price position | any | WARNING |
| Limited history | <5 snapshots | INFO |

## Modules

| File | Lines | Purpose |
|---|---|---|
| `main.py` | 151 | Entry point, env config, validation, poll loop |
| `graph.py` | 132 | LangGraph wiring — `StateGraph` construction, conditional edges |
| `nodes.py` | 570 | All 9 pipeline node implementations (`PipelineNodes` class), message builder |
| `metrics.py` | 668 | Deterministic analytics: portfolio + time-series metrics, per-stock scoring, health sub-scores |
| `policy.py` | 555 | IPS rule engine (`PolicyEngine`), `Alert`/`BucketDrift` types, `Severity` enum |
| `optimizer.py` | 402 | Rebalance candidate generation: policy rebalance, HRP risk-reduction, do-nothing |
| `analyzer.py` | 316 | OpenAI structured output with Pydantic schema (`PortfolioReport`) |
| `store.py` | 198 | JSONL snapshot persistence, time-series queries, per-cycle read cache |
| `ips.py` | 158 | IPS YAML loader, `IPSConfig`/`Bucket` frozen dataclasses |
| `tickers.py` | 108 | T212 ticker parsing → `(symbol, market)` + Finnhub symbol mapping |
| `news.py` | 102 | Finnhub client: news, fundamentals, profiles, earnings calendar |
| `notifier.py` | 75 | Telegram sender with chunking and Markdown fallback |
| `trading212.py` | 70 | T212 API client, Basic Auth, snapshot assembly |

## Tests

131 unit tests covering all modules. Run with:

```bash
cd apps/portfolio_agent
pip install -r requirements.txt
python -m pytest tests/ -v
```

| File | Tests | Covers |
|---|---|---|
| `test_nodes.py` | 621 lines | All 9 node implementations, serialisation roundtrips, message builder |
| `test_metrics.py` | 573 lines | Snapshot metrics, health sub-scores, fundamentals, valuation, time-series |
| `test_policy.py` | 379 lines | Bucket drift, concentration, cash, P/L, health, drawdown, data integrity, sorting |
| `test_optimizer.py` | 270 lines | Candidate generation, policy rebalance trades, do-nothing, field validation |
| `test_store.py` | 131 lines | Append, dedup, cache invalidation, price history, malformed line handling |
| `test_ips.py` | 117 lines | Bucket drift/breach, config loading, equity/cash bucket methods |
| `test_tickers.py` | 114 lines | Known exchanges, unknown fallbacks, Finnhub mapping, consistency |
| `test_notifier.py` | 36 lines | Message splitting, edge cases |

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `T212_API_KEY` | yes | — | Trading 212 API Key ID |
| `T212_API_SECRET` | yes | — | Trading 212 API Secret |
| `T212_BASE_URL` | no | `https://live.trading212.com/api/v0` | `demo` or `live` |
| `OPENAI_API_KEY` | yes | — | OpenAI API key |
| `OPENAI_MODEL` | no | `gpt-4o` | Model for narrative analysis |
| `TELEGRAM_BOT_TOKEN` | yes | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | yes | — | Telegram chat to notify |
| `FINNHUB_API_KEY` | no | *(empty = disabled)* | Enables news + fundamentals |
| `POLL_INTERVAL` | no | `86400` | Seconds between cycles |
| `TOP_N_FOR_NEWS` | no | `5` | Positions to fetch news for |
| `IPS_PATH` | no | bundled `ips.yml` | Custom IPS config path |
| `DATA_DIR` | no | `./data` | Snapshot storage directory (Docker: `/data`) |

## Local development

```bash
cd apps/portfolio_agent
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -v

# Single live cycle (sends to Telegram)
set -a && source .env && set +a && python3 src/main.py
```

## Deployment

Secrets in Ansible Vault, deployed as `.env` to host:

```bash
ansible-vault edit inventory/group_vars/all/vault.yml
# Add: vault_t212_api_key, vault_t212_api_secret, vault_openai_api_key,
#      vault_telegram_bot_token, vault_telegram_chat_id, vault_finnhub_api_key
make stacks
```
