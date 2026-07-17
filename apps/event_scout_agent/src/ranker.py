"""LLM relevance ranking via OpenAI Structured Outputs (local Ollama in
production, same client pattern as portfolio_agent)."""

from __future__ import annotations

import json
import logging

import httpx
from openai import OpenAI
from pydantic import BaseModel, Field

from models import Event

log = logging.getLogger(__name__)

BATCH_SIZE = 8  # keeps prompts well within localllm's 16K context


class EventRanking(BaseModel):
    event_id: int = Field(description="The numeric id of the event being scored")
    score: int = Field(description="Relevance score 0-10", ge=0, le=10)
    matched_topics: list[str] = Field(
        description="Which of the user's topics this event matches (empty if none)"
    )
    reason: str = Field(description="One short sentence explaining the score")


class RankingResult(BaseModel):
    rankings: list[EventRanking]


SYSTEM_PROMPT = """\
You are a personal event scout. You score upcoming tech events for relevance \
to one specific user so they can decide which to attend.

Scoring rules:
- 9-10: directly about one of the user's topics, in or near their city — must attend
- 7-8: strong topic match, or a broader event with clearly relevant content
- 4-6: partial topic match, or relevant topic but inconvenient location
- 1-3: weak match — generic networking, tangential technology
- 0: unrelated, or clearly not in/near the user's city (unless online events are allowed)

Rules:
- Score ONLY from the information given. Never invent event details.
- Return exactly one ranking per event, using the event's numeric id.
- matched_topics must only contain topics from the user's list.
- Keep each reason to one short sentence.\
"""


class EventRanker:
    def __init__(self, api_key: str, model: str = "qwen3:8b", base_url: str | None = None):
        # bounded connect so an unreachable LLM fails fast; long read budget
        # for slow CPU inference on localllm
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
            max_retries=1,
        )
        self.model = model

    def rank(
        self,
        events: list[Event],
        topics: list[str],
        location: str,
        include_online: bool = False,
    ) -> dict[str, EventRanking]:
        """Score events. Events in failed batches are omitted from the result —
        the caller leaves them un-seen so they retry next cycle."""
        results: dict[str, EventRanking] = {}
        for i in range(0, len(events), BATCH_SIZE):
            batch = events[i : i + BATCH_SIZE]
            rankings = self._rank_batch(batch, topics, location, include_online)
            for ranking in rankings:
                if 0 <= ranking.event_id < len(batch):
                    results[batch[ranking.event_id].uid] = ranking
                else:
                    log.warning("LLM returned out-of-range event_id %d", ranking.event_id)
        return results

    def _rank_batch(
        self,
        batch: list[Event],
        topics: list[str],
        location: str,
        include_online: bool,
    ) -> list[EventRanking]:
        prompt = self._build_prompt(batch, topics, location, include_online)
        try:
            response = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format=RankingResult,
                max_tokens=2000,
                temperature=0.1,
            )
            parsed = response.choices[0].message.parsed
            return parsed.rankings if parsed else []
        except Exception as e:
            log.error("Structured ranking failed: %s — trying plain-JSON fallback", e)
            return self._fallback_rank(prompt)

    def _fallback_rank(self, prompt: str) -> list[EventRanking]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": prompt
                        + '\n\nRespond ONLY with JSON: {"rankings": [{"event_id": 0, '
                        '"score": 0, "matched_topics": [], "reason": ""}, ...]}',
                    },
                ],
                max_tokens=2000,
                temperature=0.1,
            )
            text = response.choices[0].message.content or ""
            json_start = text.find("{")
            json_end = text.rfind("}")
            if json_start == -1 or json_end == -1:
                return []
            data = json.loads(text[json_start : json_end + 1])
            return RankingResult(**data).rankings
        except Exception as e:
            log.error("Fallback ranking also failed: %s", e)
            return []

    @staticmethod
    def _build_prompt(
        batch: list[Event],
        topics: list[str],
        location: str,
        include_online: bool,
    ) -> str:
        lines = [
            f"User's city: {location}",
            f"Online-only events allowed: {'yes' if include_online else 'no'}",
            "User's topics of interest:",
        ]
        lines.extend(f"  - {t}" for t in topics)
        lines.append("")
        lines.append(f"Score these {len(batch)} events:")
        for idx, event in enumerate(batch):
            lines.append("")
            lines.append(f"Event id: {idx}")
            lines.append(f"  Title: {event.title}")
            lines.append(f"  When: {event.start.strftime('%A %d %B %Y, %H:%M UTC')}")
            if event.location:
                lines.append(f"  Where: {event.location}")
            lines.append(f"  Source: {event.source_name}")
            if event.description:
                lines.append(f"  Description: {event.description[:400]}")
        return "\n".join(lines)
