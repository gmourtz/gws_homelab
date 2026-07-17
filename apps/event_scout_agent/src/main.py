"""Event scout — fetches event feeds, ranks new events against the user's
topics via LLM, and Telegrams a digest. Config (topics/sources/thresholds)
comes from a YAML file templated by Ansible and re-read every cycle."""

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# keep HTTP client noise out of DEBUG — the ranker logs the parsed LLM output
for _name in ("httpx", "httpcore", "openai"):
    logging.getLogger(_name).setLevel(logging.INFO)
log = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen3:8b")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
RUN_AT_HOUR = os.getenv("RUN_AT_HOUR", "5")  # daily cycle anchor hour in DISPLAY_TZ
DATA_DIR = os.getenv("DATA_DIR", "")
CONFIG_PATH = os.getenv("CONFIG_PATH", "/data/config.yml")

DISPLAY_TZ = ZoneInfo("Europe/London")


def validate_config(once: bool = False) -> None:
    """Exit early if required env vars / config are missing."""
    required = {"OPENAI_API_KEY": OPENAI_API_KEY}
    if not once:
        # --once without Telegram creds falls back to console output
        required["TELEGRAM_BOT_TOKEN"] = TELEGRAM_BOT_TOKEN
        required["TELEGRAM_CHAT_ID"] = TELEGRAM_CHAT_ID
    missing = [k for k, v in required.items() if not v]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)
    if not os.path.exists(CONFIG_PATH):
        log.error("Config file not found: %s (set CONFIG_PATH)", CONFIG_PATH)
        sys.exit(1)
    if not (RUN_AT_HOUR.isdigit() and 0 <= int(RUN_AT_HOUR) <= 23):
        log.error("RUN_AT_HOUR must be 0-23, got %r", RUN_AT_HOUR)
        sys.exit(1)


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("location", "London")
    cfg.setdefault("lookahead_days", 45)
    cfg.setdefault("min_score", 6)
    cfg.setdefault("include_online", False)
    cfg.setdefault("notes", "")
    cfg.setdefault("topics", [])
    cfg.setdefault("sources", {})
    if not cfg["topics"] or not cfg["sources"]:
        log.error("Config must define both 'topics' and 'sources'")
        sys.exit(1)
    return cfg


def seconds_until_hour(hour: int, now: datetime | None = None) -> float:
    """Seconds until the next `hour`:00 wall-clock time in DISPLAY_TZ (DST-aware)."""
    now = (now or datetime.now(DISPLAY_TZ)).astimezone(DISPLAY_TZ)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    # same-tzinfo subtraction is wall-clock and ignores DST; compare in UTC
    return (target.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()


def filter_events(events, now, lookahead_days, seen_store):
    horizon = now + timedelta(days=lookahead_days)
    return [
        e
        for e in events
        if now <= e.start <= horizon and not seen_store.is_seen(e.uid)
    ]


def build_digest(selected) -> str:
    lines = [f"📅 *{len(selected)} new event{'s' if len(selected) != 1 else ''} for you*"]
    for event, ranking in selected:
        local = event.start.astimezone(DISPLAY_TZ)
        # date-only sources (Eventbrite JSON-LD, all-day ICS) land at midnight
        if event.start.hour == 0 and event.start.minute == 0:
            when = local.strftime("%a %d %b")
        else:
            when = local.strftime("%a %d %b, %H:%M")
        lines.append("")
        lines.append(f"*{event.title}*")
        where = f" · {event.location}" if event.location else ""
        lines.append(f"📆 {when}{where}")
        topics = ", ".join(ranking.matched_topics) or "general"
        lines.append(f"⭐ {ranking.score}/10 — {topics}")
        if ranking.reason:
            lines.append(f"_{ranking.reason}_")
        lines.append(f"🔗 {event.url}")
    return "\n".join(lines)


def run_cycle(cfg, ranker, notifier, store) -> int:
    from sources import enrich_luma_descriptions, fetch_all

    now = datetime.now(timezone.utc)

    events = fetch_all(cfg["sources"])
    log.info("Fetched %d events across all sources", len(events))

    fresh = filter_events(events, now, cfg["lookahead_days"], store)
    log.info("%d new events after date/dedup filter", len(fresh))
    if not fresh:
        store.prune(now)
        store.save()
        return 0

    # only fresh events — a handful per day after the initial backfill
    enriched = enrich_luma_descriptions(fresh)
    if enriched:
        log.info("Enriched %d Luma events with full descriptions", enriched)

    rankings = ranker.rank(
        fresh, cfg["topics"], cfg["location"], cfg["include_online"], cfg["notes"]
    )
    log.info("Ranked %d/%d events", len(rankings), len(fresh))

    selected = sorted(
        (
            (e, rankings[e.uid])
            for e in fresh
            if e.uid in rankings and rankings[e.uid].score >= cfg["min_score"]
        ),
        key=lambda pair: -pair[1].score,
    )

    sent_ok = True
    if selected:
        sent_ok = notifier.send(build_digest(selected))
        if sent_ok:
            log.info("Notified %d events (min_score=%d)", len(selected), cfg["min_score"])
        else:
            log.error(
                "Digest send failed — %d selected events stay un-seen and retry next cycle",
                len(selected),
            )
    else:
        log.info("No events above min_score=%d — staying quiet", cfg["min_score"])

    # Rejected events are marked seen too (don't re-score daily); events whose
    # ranking batch failed stay un-seen and retry next cycle. If the digest send
    # failed, the selected events also stay un-seen so they're re-notified.
    selected_uids = {e.uid for e, _ in selected}
    store.mark_seen(
        [
            e
            for e in fresh
            if e.uid in rankings and (sent_ok or e.uid not in selected_uids)
        ]
    )
    store.prune(now)
    store.save()
    return len(selected) if sent_ok else 0


def main() -> None:
    once = "--once" in sys.argv
    validate_config(once)
    cfg = load_config(CONFIG_PATH)
    log.info(
        "Event scout starting — %d topics, %d ICS feeds, %d Eventbrite searches, "
        "location=%s, daily at %02d:00 %s%s",
        len(cfg["topics"]),
        len(cfg["sources"].get("ics", []) or []),
        len(cfg["sources"].get("eventbrite_searches", []) or []),
        cfg["location"],
        int(RUN_AT_HOUR),
        DISPLAY_TZ.key,
        " (single cycle)" if once else "",
    )

    from notifier import ConsoleNotifier, TelegramNotifier
    from ranker import EventRanker
    from store import SeenStore

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    else:
        log.warning("Telegram not configured — printing digests to stdout")
        notifier = ConsoleNotifier()

    ranker = EventRanker(OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL)
    store = SeenStore(DATA_DIR or None)
    log.info("Seen-store loaded: %d tracked events", len(store))

    while True:
        try:
            start = time.time()
            log.info("━" * 60)
            log.info("Starting scout cycle")

            cfg = load_config(CONFIG_PATH)
            notified = run_cycle(cfg, ranker, notifier, store)

            elapsed = time.time() - start
            log.info("Cycle complete in %.1fs — notified=%d", elapsed, notified)

            if once:
                break
            sleep_time = seconds_until_hour(int(RUN_AT_HOUR))
            log.info(
                "Next cycle at %02d:00 %s — in %.0f seconds",
                int(RUN_AT_HOUR), DISPLAY_TZ.key, sleep_time,
            )
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            try:
                notifier.send(f"⚠️ Event scout error: {e}")
            except Exception:
                pass
            if once:
                sys.exit(1)
            time.sleep(60)


if __name__ == "__main__":
    main()
