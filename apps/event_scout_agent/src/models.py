"""Shared data models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Event(BaseModel):
    """One event, normalised across all sources."""

    uid: str
    title: str
    description: str = ""
    start: datetime
    end: datetime | None = None
    location: str = ""
    url: str = ""
    source_name: str
    source_type: str  # "ics" | "eventbrite"
