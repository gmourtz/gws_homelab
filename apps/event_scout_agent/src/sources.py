"""Event source fetchers: iCalendar feeds (Meetup/Luma/generic) and
Eventbrite public search pages (embedded schema.org JSON-LD — the search
API was removed in 2020)."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import requests
from icalendar import Calendar

from models import Event

log = logging.getLogger(__name__)

# Meetup/Eventbrite serve bot-block pages to obvious non-browser agents
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
TIMEOUT = 30
# cap ingested descriptions — wide enough for full text + speaker lineups,
# while keeping the batched ranking prompt inside localllm's 16K context
MAX_DESCRIPTION_CHARS = 4000

_URL_RE = re.compile(r"https?://[^\s<>\"\\]+")
_LDJSON_RE = re.compile(
    r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)
_LUMA_URL_RE = re.compile(r"https?://(?:lu\.ma|luma\.com)/[A-Za-z0-9\-_.]+")
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


def _to_utc(value) -> datetime | None:
    """Normalise an icalendar DTSTART/DTEND value to an aware UTC datetime."""
    if value is None:
        return None
    dt = getattr(value, "dt", value)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    # all-day events carry a bare date
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


def fetch_ics(name: str, url: str) -> list[Event]:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    cal = Calendar.from_ical(resp.content)

    events: list[Event] = []
    for component in cal.walk("VEVENT"):
        start = _to_utc(component.get("DTSTART"))
        if start is None:
            continue
        summary = str(component.get("SUMMARY", "")).strip()
        description = str(component.get("DESCRIPTION", "")).strip()
        event_url = str(component.get("URL", "")).strip()
        if not event_url:
            # Meetup omits the URL property but links the event in the description
            match = _URL_RE.search(description)
            event_url = match.group(0) if match else url
        uid = str(component.get("UID", "")).strip() or event_url
        events.append(
            Event(
                uid=uid,
                title=summary,
                description=description[:MAX_DESCRIPTION_CHARS],
                start=start,
                end=_to_utc(component.get("DTEND")),
                location=str(component.get("LOCATION", "")).strip(),
                url=event_url,
                source_name=name,
                source_type="ics",
            )
        )
    log.info("[%s] %d events from ICS feed", name, len(events))
    return events


def _jsonld_location(item: dict) -> str:
    loc = item.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if not isinstance(loc, dict):
        return str(loc)
    parts = [loc.get("name", "")]
    address = loc.get("address")
    if isinstance(address, dict):
        parts.append(address.get("addressLocality", ""))
    elif isinstance(address, str):
        parts.append(address)
    return ", ".join(p for p in parts if p)


def fetch_eventbrite(name: str, search_url: str) -> list[Event]:
    resp = requests.get(search_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()

    events: list[Event] = []
    for block in _LDJSON_RE.findall(resp.text):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "ItemList":
            continue
        for entry in data.get("itemListElement", []):
            item = entry.get("item", {})
            url = item.get("url", "")
            start_raw = item.get("startDate", "")
            if not url or not start_raw:
                continue
            try:
                start = datetime.fromisoformat(start_raw)
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            end = None
            if item.get("endDate"):
                try:
                    end = datetime.fromisoformat(item["endDate"])
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            events.append(
                Event(
                    uid=url,
                    title=item.get("name", "").strip(),
                    description=str(item.get("description", ""))[:MAX_DESCRIPTION_CHARS],
                    start=start.astimezone(timezone.utc),
                    end=end.astimezone(timezone.utc) if end else None,
                    location=_jsonld_location(item),
                    url=url,
                    source_name=name,
                    source_type="eventbrite",
                )
            )
    log.info("[%s] %d events from Eventbrite page", name, len(events))
    return events


def fetch_eventbrite_price(url: str) -> float | None:
    """Eventbrite's search-results JSON-LD never carries a price (offers is
    always null there) — only an event's own page does, under an @type that
    varies by category (Event/EducationEvent/BusinessEvent/...), so this
    scans every ld+json block for the first one with an `offers` key rather
    than matching a specific @type. Returns the lowest ticket price found,
    or None if it's unknown (fetch failure or no offers block) — callers
    should treat None as "can't tell", not "free"."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Price fetch failed for %s: %s", url, e)
        return None

    prices: list[float] = []
    for block in _LDJSON_RE.findall(resp.text):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        offers = data.get("offers") if isinstance(data, dict) else None
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers, list):
            continue
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            for key in ("price", "lowPrice"):
                if key in offer:
                    try:
                        prices.append(float(offer[key]))
                    except (TypeError, ValueError):
                        pass
    return min(prices) if prices else None


def _mirror_text(node) -> str:
    """Flatten a ProseMirror document (Luma's rich-text format) to plain text."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    text = "".join(_mirror_text(c) for c in node.get("content", []) or [])
    if node.get("type") in ("paragraph", "heading", "listItem"):
        text += "\n"
    return text


def enrich_luma_descriptions(events: list[Event], delay: float = 0.3) -> int:
    """Luma ICS descriptions are stubs ("Get up-to-date information at: ...").
    Fetch each event's page and append the full description from its embedded
    __NEXT_DATA__ JSON, so the ranker sees speaker lineups and event detail.
    Returns the number of events enriched. Failures are soft per event."""
    enriched = 0
    for event in events:
        match = _LUMA_URL_RE.search(event.description) or _LUMA_URL_RE.search(event.url)
        if not match:
            continue
        try:
            resp = requests.get(match.group(0), headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            data_match = _NEXT_DATA_RE.search(resp.text)
            if not data_match:
                continue
            data = json.loads(data_match.group(1))
            mirror = data["props"]["pageProps"]["initialData"]["data"].get(
                "description_mirror"
            )
            text = _mirror_text(mirror).strip()
            if text:
                # keep the stub — its "Hosted by ..." line is signal too
                event.description = f"{event.description}\n\n{text}"[:MAX_DESCRIPTION_CHARS]
                enriched += 1
        except Exception as e:
            log.warning("Luma enrichment failed for %s: %s", event.title, e)
        if delay:
            time.sleep(delay)
    return enriched


def fetch_all(sources: dict) -> list[Event]:
    """Fetch every configured source; one broken feed never kills the cycle."""
    events: list[Event] = []
    seen_uids: set[str] = set()

    fetchers = [
        (fetch_ics, sources.get("ics", []) or []),
        (fetch_eventbrite, sources.get("eventbrite_searches", []) or []),
    ]
    for fetcher, entries in fetchers:
        for entry in entries:
            try:
                for event in fetcher(entry["name"], entry["url"]):
                    if event.uid not in seen_uids:
                        seen_uids.add(event.uid)
                        events.append(event)
            except Exception as e:
                log.error("[%s] fetch failed: %s", entry.get("name", "?"), e)

    return events
