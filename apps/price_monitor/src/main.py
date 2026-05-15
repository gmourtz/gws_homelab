"""Price monitor — watches Amazon products and alerts on price drops via Telegram.

Pipeline:
  Load watchlist → Scrape each product → Compare to last known price →
  If price dropped → LLM analysis → Telegram alert

Config is driven by:
  - WATCHLIST_PATH: JSON file with product URLs + labels
  - Environment variables for Telegram, LLM, poll interval
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen3:1.7b")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL") or "21600")  # 6 hours
DATA_DIR = os.getenv("DATA_DIR", "/data")
WATCHLIST_PATH = os.getenv("WATCHLIST_PATH", "/data/watchlist.json")


def validate_config() -> None:
    """Exit early if required env vars are missing."""
    required = {
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

def run_cycle(
    watchlist_path: str,
    data_dir: str,
    notifier,
    openai_base_url: str,
    openai_api_key: str,
    openai_model: str,
) -> dict:
    """Run one check cycle. Returns stats dict."""
    from watchlist import load_watchlist
    from scraper import scrape_product
    from store import PriceStore
    from analyzer import analyze_deal

    items = load_watchlist(watchlist_path)
    if not items:
        log.warning("Watchlist is empty — nothing to monitor")
        return {"checked": 0, "drops": 0, "errors": 0}

    store = PriceStore(data_dir)
    stats = {"checked": 0, "drops": 0, "errors": 0}

    for item in items:
        try:
            log.info("Checking: %s", item.label)
            product = scrape_product(item.url)
            stats["checked"] += 1

            if product.price is None:
                log.warning("Could not extract price for %s", item.label)
                stats["errors"] += 1
                continue

            log.info("  %s: %s %.2f", item.label, product.currency, product.price)

            # Always record the price
            store.append(item.url, product.title, product.price, product.currency)

            # Need at least 4 observations to establish a confident baseline
            # (avoids false alerts on first run or after stale data)
            history = store.get_history(item.url, limit=10)
            if len(history) < 4:
                log.info("  Collecting baseline for %s (%d/4 observations)", item.label, len(history))
                continue

            # Use the most common price across ALL history (except current) as baseline
            # The baseline must appear at least 2 times to be considered reliable
            prev_prices = [r.price for r in history[:-1]]
            price_counts = {}
            for p in prev_prices:
                price_counts[p] = price_counts.get(p, 0) + 1
            baseline = max(price_counts, key=price_counts.get)
            baseline_count = price_counts[baseline]

            if baseline_count < 2:
                log.info("  No stable baseline yet for %s — most frequent price %.2f seen only %d time(s)",
                         item.label, baseline, baseline_count)
                continue

            if product.price >= baseline:
                if product.price > baseline:
                    log.info("  Price went UP: %.2f → %.2f", baseline, product.price)
                else:
                    log.info("  Price unchanged: %.2f", product.price)
                continue

            # Price is lower — but is it stable? Check if last 2 observations
            # both show this lower price (to filter Amazon's flickering)
            recent_two = [r.price for r in history[-2:]]
            if len(set(recent_two)) != 1:
                log.info("  Price dipped to %.2f but not confirmed yet (was %.2f) — waiting for stability",
                         product.price, baseline)
                continue

            # Confirmed price drop!
            drop = baseline - product.price
            drop_pct = (drop / baseline) * 100
            stats["drops"] += 1
            log.info("  💰 CONFIRMED PRICE DROP: %.2f → %.2f (%.1f%%)", baseline, product.price, drop_pct)

            # Get LLM analysis if configured
            analysis = ""
            if openai_base_url and openai_api_key:
                analysis = analyze_deal(
                    title=product.title,
                    current_price=product.price,
                    previous_price=baseline,
                    currency=product.currency,
                    url=item.url,
                    base_url=openai_base_url,
                    api_key=openai_api_key,
                    model=openai_model,
                )

            # Build notification message
            msg_lines = [
                f"🏷️ *Price Drop: {item.label}*",
                "",
                f"📦 {product.title}",
                f"💰 {product.currency} {baseline:.2f} → *{product.currency} {product.price:.2f}*",
                f"📉 Down {product.currency} {drop:.2f} ({drop_pct:.1f}%)",
                "",
                f"🔗 {item.url}",
            ]

            if analysis:
                msg_lines.extend(["", f"🤖 _{analysis}_"])

            notifier.send("\n".join(msg_lines))

            # Small delay between products to avoid rate limiting
            time.sleep(3)

        except Exception as e:
            log.error("Error checking %s: %s", item.label, e, exc_info=True)
            stats["errors"] += 1

    return stats


def main() -> None:
    validate_config()
    log.info("Price monitor starting (interval: %ds, watchlist: %s)", POLL_INTERVAL, WATCHLIST_PATH)

    from notifier import TelegramNotifier

    notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    llm_configured = bool(OPENAI_BASE_URL and OPENAI_API_KEY)
    if llm_configured:
        log.info("LLM analysis enabled (model: %s, url: %s)", OPENAI_MODEL, OPENAI_BASE_URL)
    else:
        log.info("LLM analysis disabled — set OPENAI_BASE_URL and OPENAI_API_KEY to enable")

    while True:
        try:
            start = time.time()
            log.info("━" * 60)
            log.info("Starting price check cycle")

            stats = run_cycle(
                watchlist_path=WATCHLIST_PATH,
                data_dir=DATA_DIR,
                notifier=notifier,
                openai_base_url=OPENAI_BASE_URL,
                openai_api_key=OPENAI_API_KEY,
                openai_model=OPENAI_MODEL,
            )

            elapsed = time.time() - start
            log.info(
                "Cycle complete in %.1fs — checked=%d, drops=%d, errors=%d",
                elapsed, stats["checked"], stats["drops"], stats["errors"],
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
                notifier.send(f"⚠️ Price monitor error: {e}")
            except Exception:
                pass
            time.sleep(60)


if __name__ == "__main__":
    main()
