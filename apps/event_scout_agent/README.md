# event_scout_agent

Finds tech events in London (or wherever you point it) matching your topics
of interest, scores them with the local LLM, and sends a Telegram digest of
the best ones so you can register.

## How it works

```
config.yml (topics, sources, thresholds — templated by Ansible)
   │
   ▼
FETCH    ICS feeds (Meetup groups, Luma calendars, any iCal URL)
         + Eventbrite public search pages (embedded schema.org JSON-LD)
   ▼
FILTER   future events inside the lookahead window, not already handled
   ▼
RANK     local LLM (Ollama on localllm, OpenAI-compatible API) scores each
         new event 0-10 against your topics — structured output, batched
   ▼
NOTIFY   one Telegram digest per cycle: events ≥ min_score, sorted by score
   ▼
STORE    seen.json in /data — notify-once, pruned after the event passes
```

Why feeds instead of APIs: Meetup's API is paywalled (Pro subscription),
Eventbrite removed its public event-search API in 2020. Per-group Meetup ICS
feeds, per-calendar Luma ICS feeds, and Eventbrite's JSON-LD search markup
are all free, auth-less, and structured.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | `"ollama"` dummy value for local Ollama |
| `OPENAI_BASE_URL` | OpenAI | Set to `http://localllm.internal:11434/v1` |
| `OPENAI_MODEL` | `qwen3:8b` | Ranking model |
| `TELEGRAM_BOT_TOKEN` | — (required) | Bot token |
| `TELEGRAM_CHAT_ID` | — (required) | Destination chat |
| `RUN_AT_HOUR` | `5` | Daily cycle anchor hour (0-23, Europe/London) |
| `DATA_DIR` | `./data` | State directory (volume in production) |
| `CONFIG_PATH` | `/data/config.yml` | Topics/sources config |
| `LOG_LEVEL` | `INFO` | `DEBUG` logs every LLM ranking, including rejected events |

Topics, sources, location, and thresholds live in the YAML config — in
production it is rendered from the `event_scout_agent` var in
`inventory/group_vars/all/main.yml` by `make stacks`. Adding a source or
topic is an inventory edit, no image rebuild.

### Adding sources

- **Meetup group**: `https://www.meetup.com/<group-slug>/events/ical/`
- **Luma calendar**: `https://api.lu.ma/ics/get?entity=calendar&id=<cal-...>`
  (find the id via the calendar page's "Add iCal Subscription" button, or in
  the page source)
- **Eventbrite keyword**: `https://www.eventbrite.co.uk/d/united-kingdom--london/<keyword>/`
- **Anything else** publishing an iCal feed goes under `ics:` too.

## Local dry run

```bash
cd apps/event_scout_agent
pip install -r requirements.txt
CONFIG_PATH=config.example.yml OPENAI_API_KEY=ollama \
  OPENAI_BASE_URL=http://localllm.internal:11434/v1 \
  python src/main.py --once
```

Without `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` the digest prints to stdout.

## Tests

```bash
pip install pytest && pytest
```
