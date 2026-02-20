"""LangGraph workflow — stateful, auditable portfolio analysis pipeline.

Graph nodes (executed in order):
  1. fetch          — T212 snapshot (account + positions)
  2. validate       — Schema check, currency normalisation, data-issue flags
  3. update_store   — Persist snapshot for time-series history
  4. compute        — Portfolio metrics + time-series + per-stock scoring
  5. optimize       — Generate 2-3 deterministic rebalance candidates
  6. evaluate       — IPS rule engine → typed alerts + action_required bool
  7. research       — Fetch news + fundamentals for flagged tickers
  8. analyze        — LLM narrative (structured output, schema-locked)
  9. notify         — Assemble and send Telegram message

State is persisted so "what changed since last run" is deterministic.

Node implementations live in nodes.py (independently testable).
This module handles only graph wiring.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import StateGraph, START, END

from analyzer import PortfolioAnalyzer
from ips import IPSConfig
from news import FinnhubClient
from nodes import PipelineNodes
from notifier import TelegramNotifier
from policy import PolicyEngine
from store import SnapshotStore
from trading212 import Trading212Client

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    """Pipeline state — passed and updated across graph nodes."""

    # Raw data
    snapshot: dict | None

    # Computed
    portfolio_metrics: dict | None
    timeseries_metrics: dict | None
    stock_scores: list[dict]
    bucket_drifts: list[dict]
    rebalance_options: list[dict]
    bucket_assignments: dict
    alerts: list[dict]
    action_required: bool
    dca_signals: list[dict]
    opportunities: list[dict]

    # LLM output
    report: dict | None

    # News / fundamentals
    news: dict
    fundamentals: dict

    # Notification
    message: str
    sent: bool

    # Metadata
    errors: list[str]
    history_days: int
    persistent_state: dict


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph(
    t212_client: Trading212Client,
    finnhub_client: FinnhubClient | None,
    openai_api_key: str,
    openai_model: str,
    notifier: TelegramNotifier,
    store: SnapshotStore,
    ips: IPSConfig,
    top_n_news: int = 5,
) -> Any:
    """Build and compile the LangGraph portfolio analysis pipeline."""

    analyzer = PortfolioAnalyzer(openai_api_key, openai_model)
    policy_engine = PolicyEngine(ips)

    nodes = PipelineNodes(
        t212_client=t212_client,
        finnhub_client=finnhub_client,
        analyzer=analyzer,
        policy_engine=policy_engine,
        notifier=notifier,
        store=store,
        ips=ips,
        top_n_news=top_n_news,
    )

    builder = StateGraph(AgentState)

    builder.add_node("fetch", nodes.fetch)
    builder.add_node("validate", nodes.validate)
    builder.add_node("update_store", nodes.update_store)
    builder.add_node("compute", nodes.compute)
    builder.add_node("optimize", nodes.optimize)
    builder.add_node("evaluate_policy", nodes.evaluate_policy)
    builder.add_node("research", nodes.research)
    builder.add_node("analyze", nodes.analyze)
    builder.add_node("notify", nodes.notify)

    builder.add_edge(START, "fetch")
    builder.add_conditional_edges(
        "fetch",
        lambda s: "validate" if s.get("snapshot") else "notify",
        {"validate": "validate", "notify": "notify"},
    )
    builder.add_edge("validate", "update_store")
    builder.add_edge("update_store", "compute")
    builder.add_edge("compute", "optimize")
    builder.add_edge("optimize", "evaluate_policy")
    builder.add_edge("evaluate_policy", "research")
    builder.add_edge("research", "analyze")
    builder.add_edge("analyze", "notify")
    builder.add_edge("notify", END)

    return builder.compile()
