"""Pipeline node implementations — independently testable.

Each method on PipelineNodes corresponds to one graph node.
Dependencies are injected via constructor, making every node
callable in isolation for unit testing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import date
from typing import Any

from analyzer import PortfolioAnalyzer
from ips import IPSConfig
from metrics import (
    PortfolioMetrics,
    PositionMetric,
    TimeSeriesMetrics,
    compute_metrics,
    compute_timeseries_metrics,
)
from news import FinnhubClient, extract_symbol
from notifier import TelegramNotifier
from optimizer import generate_rebalance_options
from policy import Alert, BucketDrift, PolicyEngine, Severity
from store import DailySnapshot, PersistentState, SnapshotStore
from trading212 import Trading212Client

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialisation helpers (PortfolioMetrics ↔ dict via graph state)
# ---------------------------------------------------------------------------

def metrics_to_dict(pm: PortfolioMetrics) -> dict:
    """Convert PortfolioMetrics to a JSON-safe dict for graph state."""
    return {
        "timestamp": pm.timestamp,
        "currency": pm.currency,
        "total_value": pm.total_value,
        "total_invested": pm.total_invested,
        "free_cash": pm.free_cash,
        "cash_pct": pm.cash_pct,
        "overall_pnl": pm.overall_pnl,
        "overall_pnl_pct": pm.overall_pnl_pct,
        "realized_pnl": pm.realized_pnl,
        "num_positions": pm.num_positions,
        "hhi": pm.hhi,
        "top1_weight": pm.top1_weight,
        "top1_ticker": pm.top1_ticker,
        "top3_weight": pm.top3_weight,
        "top5_weight": pm.top5_weight,
        "market_weights": pm.market_weights,
        "positions": [asdict(p) for p in pm.positions],
        "health_score": pm.health_score,
        "health_sub": pm.health_sub,
        "winners": [asdict(p) for p in pm.winners],
        "losers": [asdict(p) for p in pm.losers],
    }


def dict_to_metrics(d: dict) -> PortfolioMetrics:
    """Reconstruct PortfolioMetrics from graph state dict."""

    def _pos(p: dict) -> PositionMetric:
        return PositionMetric(**{
            k: v for k, v in p.items()
            if k in PositionMetric.__dataclass_fields__
        })

    return PortfolioMetrics(
        timestamp=d["timestamp"],
        currency=d["currency"],
        total_value=d["total_value"],
        total_invested=d["total_invested"],
        free_cash=d["free_cash"],
        cash_pct=d["cash_pct"],
        overall_pnl=d["overall_pnl"],
        overall_pnl_pct=d["overall_pnl_pct"],
        realized_pnl=d["realized_pnl"],
        num_positions=d["num_positions"],
        hhi=d["hhi"],
        top1_weight=d["top1_weight"],
        top1_ticker=d["top1_ticker"],
        top3_weight=d["top3_weight"],
        top5_weight=d["top5_weight"],
        market_weights=d["market_weights"],
        positions=[_pos(p) for p in d.get("positions", [])],
        health_score=d["health_score"],
        health_sub=d["health_sub"],
        winners=[_pos(p) for p in d.get("winners", [])],
        losers=[_pos(p) for p in d.get("losers", [])],
    )


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def build_message(
    pm_dict: dict,
    alerts: list[dict],
    report_dict: dict | None,
    action_required: bool,
    state: dict,
) -> str:
    """Assemble Telegram message: deterministic header + structured AI report."""
    parts: list[str] = []

    # ── Header (deterministic) ──
    health = pm_dict.get("health_score", 0)
    if health >= 70:
        emoji = "🟢"
    elif health >= 50:
        emoji = "🟡"
    else:
        emoji = "🔴"

    sub = pm_dict.get("health_sub", {})
    sub_str = "  ".join(f"{k[:3].title()}:{v}/25" for k, v in sub.items())
    parts.append(f"{emoji} Portfolio Health: {health}/100\n{sub_str}")

    # ── Alert summary ──
    action_count = sum(1 for a in alerts if "ACTION" in a.get("severity", ""))
    warning_count = sum(1 for a in alerts if "WARNING" in a.get("severity", ""))

    if action_required:
        parts.append(f"\n🚨 {action_count} action(s) required, {warning_count} warning(s)")
        for a in alerts:
            if "ACTION" in a.get("severity", ""):
                parts.append(f"  🔴 {a.get('title', '')}")
                parts.append(f"     Rule: {a.get('rule', '')}")
    elif warning_count:
        parts.append(f"\n⚠️ {warning_count} warning(s)")
        for a in alerts:
            if "WARNING" in a.get("severity", ""):
                parts.append(f"  🟡 {a.get('title', '')}")
    else:
        parts.append("\n✅ All clear — IPS fully compliant")

    # ── Quick stats ──
    parts.append(
        f"\n📊 P/L: {pm_dict.get('overall_pnl_pct', 0):+.1f}% | "
        f"Cash: {pm_dict.get('cash_pct', 0):.1f}% | "
        f"Positions: {pm_dict.get('num_positions', 0)}"
    )

    # ── Time-series ──
    ts = state.get("timeseries_metrics")
    if ts:
        ts_parts = []
        if ts.get("sharpe_ratio") is not None:
            ts_parts.append(f"Sharpe: {ts['sharpe_ratio']:.2f}")
        if ts.get("max_drawdown_pct") is not None:
            ts_parts.append(f"MaxDD: {ts['max_drawdown_pct']:.1f}%")
        if ts.get("annual_volatility_pct") is not None:
            ts_parts.append(f"Vol: {ts['annual_volatility_pct']:.1f}%")
        if ts_parts:
            parts.append(f"📈 {' | '.join(ts_parts)}")

    # ── Bucket drifts ──
    drifts = state.get("bucket_drifts", [])
    breached = [d for d in drifts if d.get("breached")]
    if breached:
        parts.append("\n🎯 Allocation drifts (breached):")
        for d in breached:
            parts.append(
                f"  {d['bucket_name']}: {d['actual_pct']:.1f}% "
                f"(target {d['target_pct']:.0f}%, {d['drift_pct']:+.1f}pp)"
            )

    # ── AI report (structured) ──
    if report_dict:
        parts.append("\n🎯 Assessment")

        # Executive summary
        for bullet in report_dict.get("executive_summary", []):
            parts.append(f"• {bullet}")

        # Alert explanations
        explanations = report_dict.get("alert_explanations", [])
        if explanations:
            parts.append("\n📋 Alert details:")
            for ae in explanations:
                parts.append(f"  [{ae.get('severity', '?')}] {ae.get('alert_title', '')}")
                parts.append(f"  → {ae.get('why_it_matters', '')}")

        # Rebalance options
        options = report_dict.get("options", [])
        if options:
            parts.append("\n⚖️ Options:")
            for opt in options:
                parts.append(f"  {opt.get('name', '')}: {opt.get('recommendation', '')}")

        # Watchlist
        watchlist = report_dict.get("watchlist", [])
        if watchlist:
            parts.append("\n👁️ Watch:")
            for w in watchlist:
                parts.append(f"  • {w.get('item', '')} → {w.get('trigger', '')}")

        # Caveats
        caveats = report_dict.get("caveats", [])
        if caveats:
            parts.append(f"\n_{caveats[-1]}_")  # Last caveat (always "not financial advice")
    else:
        parts.append("\n⚠️ AI analysis unavailable")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pipeline nodes
# ---------------------------------------------------------------------------

class PipelineNodes:
    """All graph node implementations with explicit dependencies.

    Each method corresponds to one LangGraph node and can be
    called independently for unit testing.
    """

    def __init__(
        self,
        t212_client: Trading212Client,
        finnhub_client: FinnhubClient | None,
        analyzer: PortfolioAnalyzer,
        policy_engine: PolicyEngine,
        notifier: TelegramNotifier,
        store: SnapshotStore,
        ips: IPSConfig,
        top_n_news: int = 5,
    ):
        self.t212_client = t212_client
        self.finnhub_client = finnhub_client
        self.analyzer = analyzer
        self.policy_engine = policy_engine
        self.notifier = notifier
        self.store = store
        self.ips = ips
        self.top_n_news = top_n_news

    # ── Node 1: Fetch ──────────────────────────────────────────────

    def fetch(self, state: dict) -> dict:
        """Fetch portfolio snapshot from Trading 212."""
        log.info("=== [1/9] Fetching portfolio snapshot ===")
        try:
            snapshot = self.t212_client.get_portfolio_snapshot()
            if snapshot is None:
                return {"snapshot": None, "errors": ["T212 API returned no data"]}
            log.info("Fetched %d positions", len(snapshot.get("positions", [])))
            return {"snapshot": snapshot, "errors": []}
        except Exception as e:
            log.error("Fetch failed: %s", e)
            return {"snapshot": None, "errors": [f"Fetch error: {e}"]}

    # ── Node 2: Validate ───────────────────────────────────────────

    def validate(self, state: dict) -> dict:
        """Validate snapshot schema and normalise data."""
        log.info("=== [2/9] Validating snapshot ===")
        snapshot = state.get("snapshot")
        errors = list(state.get("errors", []))

        if not snapshot:
            errors.append("No snapshot to validate")
            return {"errors": errors}

        # Basic schema check
        required = ["account", "cash", "positions"]
        for key in required:
            if key not in snapshot:
                errors.append(f"Missing '{key}' in snapshot")

        positions = snapshot.get("positions", [])
        if not positions:
            errors.append("Empty positions list")

        if errors:
            log.warning("Validation issues: %s", errors)

        return {"errors": errors}

    # ── Node 3: Update store ───────────────────────────────────────

    def update_store(self, state: dict) -> dict:
        """Persist snapshot for time-series history."""
        log.info("=== [3/9] Updating store ===")
        snapshot = state.get("snapshot")
        if not snapshot:
            return {"history_days": 0}

        positions = snapshot.get("positions", [])
        cash_data = snapshot.get("cash", {})

        pos_values: dict[str, float] = {}
        pos_weights: dict[str, float] = {}
        pos_prices: dict[str, float] = {}
        total_value = cash_data.get("totalValue", 0)

        for p in positions:
            instrument = p.get("instrument", {})
            ticker = instrument.get("ticker", "UNKNOWN")
            wallet = p.get("walletImpact") or {}
            value = wallet.get("currentValue") or (
                p.get("quantity", 0) * p.get("currentPrice", 0)
            )
            pos_values[ticker] = value
            pos_weights[ticker] = (value / total_value * 100) if total_value else 0
            pos_prices[ticker] = p.get("currentPrice", 0)

        snap = DailySnapshot(
            date=date.today().isoformat(),
            total_value=total_value,
            invested=cash_data.get("invested", 0),
            cash=cash_data.get("free", 0),
            positions=pos_values,
            weights=pos_weights,
            prices=pos_prices,
        )
        self.store.append_snapshot(snap)

        # Load persistent state
        ps = self.store.load_state()
        count = self.store.snapshot_count()
        log.info("Store has %d snapshots", count)

        return {
            "history_days": count,
            "persistent_state": asdict(ps),
        }

    # ── Node 4: Compute metrics ────────────────────────────────────

    def compute(self, state: dict) -> dict:
        """Portfolio + time-series + per-stock metrics."""
        log.info("=== [4/9] Computing metrics ===")
        snapshot = state.get("snapshot")
        if not snapshot:
            return {}

        # Snapshot metrics
        pm = compute_metrics(snapshot)
        log.info(
            "Metrics: health=%d/100, HHI=%.0f, P/L=%+.1f%%",
            pm.health_score, pm.hhi, pm.overall_pnl_pct,
        )

        # Time-series metrics
        returns = self.store.get_returns()
        values = self.store.get_values()
        price_hist = self.store.get_price_history()
        ts = compute_timeseries_metrics(returns, values, price_hist)
        if ts:
            log.info(
                "Time-series: %d days, ann_return=%s%%, sharpe=%s",
                ts.history_days,
                ts.annual_return_pct,
                ts.sharpe_ratio,
            )

        return {
            "portfolio_metrics": metrics_to_dict(pm),
            "timeseries_metrics": asdict(ts) if ts else None,
        }

    # ── Node 5: Optimise ───────────────────────────────────────────

    def optimize(self, state: dict) -> dict:
        """Generate deterministic rebalance candidates."""
        log.info("=== [5/9] Generating rebalance options ===")
        pm_dict = state.get("portfolio_metrics")
        if not pm_dict:
            return {"rebalance_options": []}

        pm = dict_to_metrics(pm_dict)

        # Build bucket assignments
        bucket_assignments: dict[str, str] = {}
        bucket_targets: dict[str, float] = {}
        for bucket in self.ips.buckets:
            bucket_targets[bucket.name] = bucket.target_pct
            if bucket.type == "cash":
                continue
            assigned_markets: set[str] = set()
            for b in self.ips.buckets:
                if b.type != "cash" and "*" not in b.markets:
                    assigned_markets.update(b.markets)

            for p in pm.positions:
                if "*" in bucket.markets:
                    if p.market not in assigned_markets and p.ticker not in bucket_assignments:
                        bucket_assignments[p.ticker] = bucket.name
                elif p.market in bucket.markets and p.ticker not in bucket_assignments:
                    bucket_assignments[p.ticker] = bucket.name

        current_weights = {p.ticker: p.weight_pct for p in pm.positions}
        position_names = {p.ticker: p.name for p in pm.positions}
        cash_bucket = self.ips.cash_bucket()
        cash_target = cash_bucket.target_pct if cash_bucket else 5.0

        price_hist = self.store.get_price_history()

        options = generate_rebalance_options(
            current_weights=current_weights,
            bucket_assignments=bucket_assignments,
            bucket_targets=bucket_targets,
            cash_pct=pm.cash_pct,
            cash_target_pct=cash_target,
            position_names=position_names,
            price_history=price_hist,
        )

        log.info("Generated %d rebalance options", len(options))
        return {"rebalance_options": [asdict(o) for o in options]}

    # ── Node 6: Evaluate policy ────────────────────────────────────

    def evaluate_policy(self, state: dict) -> dict:
        """Run IPS policy checks -> alerts + bucket drifts."""
        log.info("=== [6/9] Evaluating policy ===")
        pm_dict = state.get("portfolio_metrics")
        if not pm_dict:
            return {"alerts": [], "bucket_drifts": [], "action_required": False}

        pm = dict_to_metrics(pm_dict)
        ts_dict = state.get("timeseries_metrics")
        ts = TimeSeriesMetrics(**ts_dict) if ts_dict else None

        ps = state.get("persistent_state", {})
        last_reb = ps.get("last_rebalance_date")
        last_rev = ps.get("last_review_date")

        alerts, bucket_drifts, action_required = self.policy_engine.evaluate(
            pm, ts_metrics=ts,
            last_rebalance_date=last_reb,
            last_review_date=last_rev,
        )

        log.info(
            "Policy: %d alerts (%d ACTION, %d WARNING, %d INFO), action_required=%s",
            len(alerts),
            sum(1 for a in alerts if a.severity == Severity.ACTION),
            sum(1 for a in alerts if a.severity == Severity.WARNING),
            sum(1 for a in alerts if a.severity == Severity.INFO),
            action_required,
        )

        return {
            "alerts": [asdict(a) for a in alerts],
            "bucket_drifts": [asdict(bd) for bd in bucket_drifts],
            "action_required": action_required,
        }

    # ── Node 7: Research ───────────────────────────────────────────

    def research(self, state: dict) -> dict:
        """Fetch news + fundamentals for flagged and top-weight tickers."""
        log.info("=== [7/9] Fetching research data ===")
        if not self.finnhub_client:
            return {"news": {}, "fundamentals": {}}

        pm_dict = state.get("portfolio_metrics")
        if not pm_dict:
            return {"news": {}, "fundamentals": {}}

        alerts = state.get("alerts", [])
        pm = dict_to_metrics(pm_dict)

        # Combine alert tickers + top positions
        alert_tickers: set[str] = set()
        for a in alerts:
            for t in a.get("tickers", []):
                alert_tickers.add(t)

        top_tickers = [p.ticker for p in pm.positions[:self.top_n_news]]
        target_tickers = list(alert_tickers | set(top_tickers))[:self.top_n_news]

        news_data: dict[str, list] = {}
        fund_data: dict[str, dict | None] = {}

        for ticker in target_tickers:
            symbol = extract_symbol(ticker)
            log.info("Fetching news + fundamentals: %s → %s", ticker, symbol)
            news_data[symbol] = self.finnhub_client.get_company_news(symbol, days_back=3)
            fund_data[symbol] = self.finnhub_client.get_basic_financials(symbol)
            time.sleep(0.3)  # rate limit

        return {"news": news_data, "fundamentals": fund_data}

    # ── Node 8: Analyse ────────────────────────────────────────────

    def analyze(self, state: dict) -> dict:
        """LLM structured narrative (only if alerts or scheduled report)."""
        log.info("=== [8/9] Generating AI analysis ===")
        pm_dict = state.get("portfolio_metrics")
        if not pm_dict:
            return {"report": None}

        pm = dict_to_metrics(pm_dict)
        ts_dict = state.get("timeseries_metrics")
        ts = TimeSeriesMetrics(**ts_dict) if ts_dict else None

        # Reconstruct Alert objects
        alert_dicts = state.get("alerts", [])
        alerts = [
            Alert(
                severity=Severity(a["severity"]),
                category=a["category"],
                title=a["title"],
                detail=a["detail"],
                rule=a["rule"],
                tickers=a.get("tickers", []),
            )
            for a in alert_dicts
        ]

        bd_dicts = state.get("bucket_drifts", [])
        bucket_drifts = [BucketDrift(**bd) for bd in bd_dicts]

        report = self.analyzer.analyze(
            metrics=pm,
            alerts=alerts,
            bucket_drifts=bucket_drifts,
            ts_metrics=ts,
            rebalance_options=state.get("rebalance_options"),
            news=state.get("news") or None,
            fundamentals=state.get("fundamentals") or None,
        )

        return {"report": report.model_dump() if report else None}

    # ── Node 9: Notify ─────────────────────────────────────────────

    def notify(self, state: dict) -> dict:
        """Assemble structured Telegram message and send."""
        log.info("=== [9/9] Sending notification ===")

        errors = state.get("errors", [])
        snapshot = state.get("snapshot")

        # Error-only notification
        if not snapshot or [e for e in errors if "Fetch" in e or "Missing" in e]:
            msg = "⚠️ Portfolio agent: could not fetch data.\n" + "\n".join(errors)
            self.notifier.send(msg)
            return {"message": msg, "sent": True}

        pm_dict = state.get("portfolio_metrics", {})
        report_dict = state.get("report")
        alerts = state.get("alerts", [])
        action_required = state.get("action_required", False)

        message = build_message(pm_dict, alerts, report_dict, action_required, state)

        self.notifier.send(message)

        # Update persistent state
        ps = PersistentState(
            **(state.get("persistent_state") or {})
        )
        ps = self.store.mark_run(ps)
        self.store.save_state(ps)

        log.info("=== Pipeline complete ===")
        return {"message": message, "sent": True}
