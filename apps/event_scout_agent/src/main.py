"""Event scout — fetches event feeds, ranks new events against the user's
topics via LLM, and Telegrams a digest to each recipient independently. Config
(topics/sources/thresholds/recipients) comes from a YAML file templated by
Ansible and re-read every cycle."""

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
RUN_AT_HOUR = os.getenv("RUN_AT_HOUR", "5")  # daily cycle anchor hour in DISPLAY_TZ
DATA_DIR = os.getenv("DATA_DIR", "")
CONFIG_PATH = os.getenv("CONFIG_PATH", "/data/config.yml")

DISPLAY_TZ = ZoneInfo("Europe/London")

# Fallback subscriber for local --once dry runs with no Telegram creds: prints
# the digest and behaves like a fresh backfilling recipient (shows everything).
CONSOLE_RECIPIENT = {"name": "console", "bot_token": "", "chat_id": "", "backfill": True}


def validate_config() -> None:
    """Exit early if required env vars / config are missing."""
    if not OPENAI_API_KEY:
        log.error("Missing required env var: OPENAI_API_KEY")
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
    cfg.setdefault("recipients", [])
    if not cfg["topics"] or not cfg["sources"]:
        log.error("Config must define both 'topics' and 'sources'")
        sys.exit(1)
    return cfg


def resolve_recipients(cfg: dict) -> list[dict]:
    """Turn config recipient entries into send-ready recipients, pulling each
    one's bot token + chat id from the env vars it names. Entries whose creds
    are unset (e.g. a friend not yet added to the vault) are skipped, so they
    stay inert until configured."""
    recipients = []
    for entry in cfg.get("recipients", []):
        token = os.environ.get(entry.get("token_env", ""), "")
        chat = os.environ.get(entry.get("chat_env", ""), "")
        if token and chat:
            recipients.append(
                {
                    "name": entry["name"],
                    "bot_token": token,
                    "chat_id": chat,
                    "backfill": bool(entry.get("backfill", False)),
                }
            )
    return recipients


def seconds_until_hour(hour: int, now: datetime | None = None) -> float:
    """Seconds until the next `hour`:00 wall-clock time in DISPLAY_TZ (DST-aware)."""
    now = (now or datetime.now(DISPLAY_TZ)).astimezone(DISPLAY_TZ)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    # same-tzinfo subtraction is wall-clock and ignores DST; compare in UTC
    return (target.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()


def filter_events(events, now, lookahead_days):
    """Keep future events inside the lookahead window. Per-recipient dedup is
    applied later — the same event may be pending for different subscribers."""
    horizon = now + timedelta(days=lookahead_days)
    return [e for e in events if now <= e.start <= horizon]


def drop_paid_eventbrite(events, store):
    """Fetch each unranked Eventbrite event's own page for its ticket price
    (the search-results feed never carries one) and tombstone paid ones —
    score 0, never ranked or sent — instead of leaving it to the LLM, which
    scores workshop/masterclass listings on title alone and misses ones that
    are actually paid. Free/unknown-price and non-Eventbrite events pass
    through unchanged."""
    from ranker import EventRanking
    from sources import fetch_eventbrite_price

    kept = []
    dropped = 0
    for event in events:
        if event.source_type != "eventbrite":
            kept.append(event)
            continue
        price = fetch_eventbrite_price(event.url)
        if price:
            store.add_rankings(
                [event],
                {
                    event.uid: EventRanking(
                        event_id=0,
                        score=0,
                        matched_topics=[],
                        reason=f"Paid event (from {price:.0f}) — filtered out before ranking",
                    )
                },
            )
            dropped += 1
        else:
            kept.append(event)
    if dropped:
        log.info("Filtered %d paid Eventbrite events before ranking", dropped)
    return kept


def drop_non_local_luma(events, location, store):
    """Drop Luma events whose page-resolved venue (set by
    enrich_luma_descriptions, called just before this) doesn't mention the
    user's city. Events with no resolved location — online events, ones
    Luma didn't geocode, or a failed page fetch — pass through untouched;
    those are still the LLM's call. This only catches what it already gets
    wrong: a real, known address in a different city."""
    from ranker import EventRanking
    from sources import is_luma_url

    kept = []
    dropped = 0
    for event in events:
        if not is_luma_url(event.url) or not event.location or location.lower() in event.location.lower():
            kept.append(event)
            continue
        store.add_rankings(
            [event],
            {
                event.uid: EventRanking(
                    event_id=0,
                    score=0,
                    matched_topics=[],
                    reason=f"Not near {location} — resolved venue is {event.location}",
                )
            },
        )
        dropped += 1
    if dropped:
        log.info("Filtered %d Luma events outside %s", dropped, location)
    return kept


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
        topics = ", ".join(ranking["matched_topics"]) or "general"
        lines.append(f"⭐ {ranking['score']}/10 — {topics}")
        if ranking["reason"]:
            lines.append(f"_{ranking['reason']}_")
        lines.append(f"🔗 {event.url}")
    return "\n".join(lines)


def run_cycle(cfg, ranker, notifier, store) -> int:
    from sources import enrich_luma_descriptions, fetch_all

    now = datetime.now(timezone.utc)

    events = fetch_all(cfg["sources"])
    log.info("Fetched %d events across all sources", len(events))

    candidates = filter_events(events, now, cfg["lookahead_days"])
    log.info("%d events within the %d-day window", len(candidates), cfg["lookahead_days"])
    if not candidates:
        store.prune(now)
        store.save()
        return 0

    # Rank only never-seen events (global rank-once cache); enrich just those.
    unranked = [e for e in candidates if not store.is_ranked(e.uid)]
    unranked = drop_paid_eventbrite(unranked, store)
    if unranked:
        enriched = enrich_luma_descriptions(unranked)
        if enriched:
            log.info("Enriched %d Luma events with full descriptions", enriched)
        unranked = drop_non_local_luma(unranked, cfg["location"], store)
    if unranked:
        new_rankings = ranker.rank(
            unranked, cfg["topics"], cfg["location"], cfg["include_online"], cfg["notes"]
        )
        store.add_rankings(unranked, new_rankings)
        log.info("Ranked %d/%d new events", len(new_rankings), len(unranked))

    recipients = resolve_recipients(cfg) or [CONSOLE_RECIPIENT]

    total_sent = 0
    for recipient in recipients:
        name = recipient["name"]
        if not store.knows_recipient(name):
            store.init_recipient(name, candidates, recipient["backfill"])
            log.info("Registered recipient %r (backfill=%s)", name, recipient["backfill"])

        pending = []
        for event in candidates:
            meta = store.ranking(event.uid)
            if meta and meta["score"] >= cfg["min_score"] and not store.is_delivered(name, event.uid):
                pending.append((event, meta))
        pending.sort(key=lambda pair: -pair[1]["score"])

        if not pending:
            log.info("Nothing new for %r", name)
            continue

        if notifier.send(recipient, build_digest(pending)):
            store.mark_delivered(name, [e for e, _ in pending])
            total_sent += len(pending)
            log.info("Notified %r of %d events (min_score=%d)", name, len(pending), cfg["min_score"])
        else:
            log.error(
                "Send to %r failed — %d events stay pending, retry next cycle",
                name, len(pending),
            )

    store.prune(now)
    store.save()
    return total_sent


def main() -> None:
    once = "--once" in sys.argv
    validate_config()
    cfg = load_config(CONFIG_PATH)

    from notifier import ConsoleNotifier, TelegramNotifier
    from ranker import EventRanker
    from store import EventStore

    recipients = resolve_recipients(cfg)
    log.info(
        "Event scout starting — %d topics, %d ICS feeds, %d Eventbrite searches, "
        "%d recipient(s), location=%s, daily at %02d:00 %s%s",
        len(cfg["topics"]),
        len(cfg["sources"].get("ics", []) or []),
        len(cfg["sources"].get("eventbrite_searches", []) or []),
        len(recipients),
        cfg["location"],
        int(RUN_AT_HOUR),
        DISPLAY_TZ.key,
        " (single cycle)" if once else "",
    )

    if recipients:
        notifier = TelegramNotifier()
    elif once:
        log.warning("No Telegram recipients configured — printing digests to stdout")
        notifier = ConsoleNotifier()
    else:
        log.error("No Telegram recipients resolved — set recipient creds and restart")
        sys.exit(1)

    ranker = EventRanker(OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL)
    store = EventStore(DATA_DIR or None)
    log.info("Store loaded: %d ranked events tracked", len(store))

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
            # best-effort crash alert to the owner only (first resolved recipient)
            try:
                alert = resolve_recipients(cfg)
                if alert:
                    notifier.send(alert[0], f"⚠️ Event scout error: {e}")
            except Exception:
                pass
            if once:
                sys.exit(1)
            time.sleep(60)


if __name__ == "__main__":
    main()
