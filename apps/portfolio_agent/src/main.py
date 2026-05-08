"""Portfolio agent — IPS-governed, LangGraph-orchestrated portfolio monitor.

Pipeline (9 graph nodes):
  Fetch → Validate → Store → Compute → Optimise → Policy → Research → Analyse → Notify

The LLM never sees raw API data or decides what to flag.
All rules, thresholds, and allocation targets are driven by the IPS config.
The agent is read-only — it never places orders.
"""

import os
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
T212_API_KEY = os.getenv("T212_API_KEY", "")
T212_API_SECRET = os.getenv("T212_API_SECRET", "")
T212_BASE_URL = os.getenv("T212_BASE_URL", "https://live.trading212.com/api/v0")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen3:8b")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL") or "86400")
TOP_N_FOR_NEWS = int(os.getenv("TOP_N_FOR_NEWS") or "5")
IPS_PATH = os.getenv("IPS_PATH", "")
DATA_DIR = os.getenv("DATA_DIR", "")


def validate_config() -> None:
    """Exit early if required env vars are missing."""
    required = {
        "T212_API_KEY": T212_API_KEY,
        "T212_API_SECRET": T212_API_SECRET,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    validate_config()
    log.info("Portfolio agent starting (interval: %ds)", POLL_INTERVAL)

    # Late imports to keep startup fast and allow env validation first
    from trading212 import Trading212Client
    from news import FinnhubClient
    from notifier import TelegramNotifier
    from store import SnapshotStore
    from ips import load_ips
    from graph import build_graph

    # --- Clients ---
    t212 = Trading212Client(T212_API_KEY, T212_API_SECRET, T212_BASE_URL)
    finnhub = FinnhubClient(FINNHUB_API_KEY) if FINNHUB_API_KEY else None
    notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    store = SnapshotStore(DATA_DIR or None)
    ips = load_ips(IPS_PATH or None)

    log.info("IPS v%d loaded: %d buckets, base=%s", ips.version, len(ips.buckets), ips.base_currency)
    if finnhub:
        log.info("Finnhub enabled — news + fundamentals included")
    else:
        log.info("Finnhub not configured — skipping news/fundamentals")

    # --- Build graph ---
    graph = build_graph(
        t212_client=t212,
        finnhub_client=finnhub,
        openai_api_key=OPENAI_API_KEY,
        openai_model=OPENAI_MODEL,
        openai_base_url=OPENAI_BASE_URL,
        notifier=notifier,
        store=store,
        ips=ips,
        top_n_news=TOP_N_FOR_NEWS,
    )
    log.info("LangGraph pipeline compiled (%d nodes)", len(graph.nodes))

    # --- Initial state ---
    initial_state = {
        "snapshot": None,
        "portfolio_metrics": None,
        "timeseries_metrics": None,
        "stock_scores": [],
        "bucket_drifts": [],
        "rebalance_options": [],
        "bucket_assignments": {},
        "alerts": [],
        "action_required": False,
        "dca_signals": [],
        "opportunities": [],
        "report": None,
        "news": {},
        "fundamentals": {},
        "message": "",
        "sent": False,
        "errors": [],
        "history_days": 0,
        "persistent_state": {},
    }

    # --- Run loop ---
    while True:
        try:
            start = time.time()
            log.info("━" * 60)
            log.info("Starting analysis cycle")

            result = graph.invoke(dict(initial_state))

            elapsed = time.time() - start
            log.info(
                "Cycle complete in %.1fs — sent=%s, action_required=%s",
                elapsed,
                result.get("sent", False),
                result.get("action_required", False),
            )

            sleep_time = max(0, POLL_INTERVAL - elapsed)
            log.info("Next cycle in %.0f seconds", sleep_time)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            try:
                notifier.send(f"⚠️ Portfolio agent error: {e}")
            except Exception:
                pass
            time.sleep(60)


if __name__ == "__main__":
    main()
