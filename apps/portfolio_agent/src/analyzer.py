"""Structured LLM narrative layer.

The LLM receives ONLY pre-computed metrics, policy alerts, and
rebalance options.  It returns a schema-locked Pydantic model —
it cannot invent fields, break the pipeline, or exceed boundaries.

Uses OpenAI Structured Outputs (response_format) for type-safe parsing.
"""

from __future__ import annotations

import json
import logging

from openai import OpenAI
from pydantic import BaseModel, Field

from metrics import PortfolioMetrics, TimeSeriesMetrics
from policy import Alert, BucketDrift

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema (Pydantic models → JSON schema → OpenAI Structured Outputs)
# ---------------------------------------------------------------------------

class AlertExplanation(BaseModel):
    """Why an alert matters and what can be done about it."""

    alert_title: str = Field(description="Title of the triggered alert")
    why_it_matters: str = Field(
        description="1-2 sentences explaining significance for the investor"
    )
    severity: str = Field(description="ACTION | WARNING | INFO")


class RebalanceOption(BaseModel):
    """One of the deterministic rebalance candidates with commentary."""

    name: str = Field(description="Option name (e.g. 'Policy rebalance')")
    recommendation: str = Field(
        description="1-2 sentence plain-English explanation of this option"
    )
    pros: list[str] = Field(description="Advantages of this approach")
    cons: list[str] = Field(description="Disadvantages or risks")
    policy_impact: str = Field(description="Effect on IPS compliance")


class WatchlistItem(BaseModel):
    """Something to monitor before the next check."""

    item: str = Field(description="What to watch")
    trigger: str = Field(description="Condition that would escalate this")


class PortfolioReport(BaseModel):
    """Complete structured analysis — schema-locked LLM output."""

    executive_summary: list[str] = Field(
        description="Exactly 3 bullet points: overall status, key risk, top opportunity",
        min_length=1,
        max_length=5,
    )
    alert_explanations: list[AlertExplanation] = Field(
        description="Explanation for each triggered alert (ACTION and WARNING only)"
    )
    options: list[RebalanceOption] = Field(
        description="2-3 rebalance options with commentary",
        min_length=1,
        max_length=4,
    )
    watchlist: list[WatchlistItem] = Field(
        description="3-5 items to monitor before next check",
        min_length=1,
        max_length=6,
    )
    caveats: list[str] = Field(
        description="Explicit limitations and disclaimers (e.g. 'FX estimates are point-in-time')",
        min_length=1,
        max_length=4,
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert portfolio analyst for a personal, self-directed investor.

You receive pre-computed portfolio metrics, IPS (Investment Policy Statement) \
alerts, bucket drift analysis, and deterministic rebalance options.

Your job is to EXPLAIN and CONTEXTUALISE — never to decide what to flag \
(the policy engine already did that) or to invent new data.

Guidelines:
- executive_summary: exactly 3 bullets — (1) overall portfolio status + urgency, \
  (2) the single biggest risk, (3) the top opportunity or positive signal
- alert_explanations: for each ACTION/WARNING alert, explain WHY it matters \
  for this specific investor.  Skip INFO alerts.
- options: restate each rebalance candidate in plain English, add your \
  assessment of timing suitability (e.g. "suitable now" vs "wait for earnings")
- watchlist: 3-5 concrete things to monitor, each with an explicit trigger
- caveats: be honest about data limitations (FX, fundamentals availability, \
  short history, etc.)

Rules:
- No raw numbers unless essential for context.
- Never recommend exact buy/sell quantities.
- Suggest direction + reasoning, never a single "do this" command.
- Focus on the 3-5 most important insights.
- If no alerts: say the portfolio is healthy and keep it very brief.
- Be direct, skip pleasantries.
- Final caveat must always include: "AI analysis — not financial advice"\
"""


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class PortfolioAnalyzer:
    """Sends curated data to OpenAI for structured narrative analysis."""

    def __init__(self, api_key: str, model: str = "qwen3:8b", base_url: str | None = None):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def analyze(
        self,
        metrics: PortfolioMetrics,
        alerts: list[Alert],
        bucket_drifts: list[BucketDrift] | None = None,
        ts_metrics: TimeSeriesMetrics | None = None,
        rebalance_options: list[dict] | None = None,
        news: dict | None = None,
        fundamentals: dict | None = None,
        stock_scores: list[dict] | None = None,
        dca_signals: list[dict] | None = None,
        opportunities: list[dict] | None = None,
    ) -> PortfolioReport | None:
        """Generate structured analysis.  Returns a Pydantic model or None."""
        prompt = self._build_prompt(
            metrics, alerts, bucket_drifts, ts_metrics, rebalance_options, news, fundamentals,
            stock_scores, dca_signals, opportunities,
        )
        log.info("Sending curated metrics to %s (structured output)…", self.model)
        log.debug("Prompt length: %d chars", len(prompt))

        try:
            response = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format=PortfolioReport,
                max_tokens=2000,
                temperature=0.3,
            )
            report = response.choices[0].message.parsed
            log.info("Structured analysis complete")
            return report
        except Exception as e:
            log.error("OpenAI structured output failed: %s", e)
            # Fallback: try unstructured and manually build a minimal report
            return self._fallback_analyze(prompt)

    def _fallback_analyze(self, prompt: str) -> PortfolioReport | None:
        """Fallback if structured output fails — returns minimal report."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0.3,
            )
            text = response.choices[0].message.content or ""
            return PortfolioReport(
                executive_summary=[text[:500] if text else "Analysis unavailable"],
                alert_explanations=[],
                options=[],
                watchlist=[WatchlistItem(item="Review full report manually", trigger="N/A")],
                caveats=["Structured output failed — free-text fallback used",
                         "AI analysis — not financial advice"],
            )
        except Exception as e:
            log.error("Fallback analysis also failed: %s", e)
            return None

    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        metrics: PortfolioMetrics,
        alerts: list[Alert],
        bucket_drifts: list[BucketDrift] | None,
        ts_metrics: TimeSeriesMetrics | None,
        rebalance_options: list[dict] | None,
        news: dict | None,
        fundamentals: dict | None,
        stock_scores: list[dict] | None = None,
        dca_signals: list[dict] | None = None,
        opportunities: list[dict] | None = None,
    ) -> str:
        lines: list[str] = []

        # --- Snapshot metrics ---
        lines.extend([
            f"Portfolio health: {metrics.health_score}/100",
            "Sub-scores: "
            + ", ".join(f"{k}={v}/25" for k, v in metrics.health_sub.items()),
            f"Positions: {metrics.num_positions} | "
            f"P/L: {metrics.overall_pnl_pct:+.1f}% | "
            f"Cash: {metrics.cash_pct:.1f}%",
            f"Concentration: HHI={metrics.hhi:.0f}, "
            f"top-1={metrics.top1_weight:.1f}%, top-3={metrics.top3_weight:.1f}%",
            "Markets: "
            + ", ".join(
                f"{m}={w:.0f}%"
                for m, w in sorted(
                    metrics.market_weights.items(), key=lambda x: -x[1]
                )
            ),
            "",
        ])

        # --- Time-series metrics ---
        if ts_metrics:
            lines.append("TIME-SERIES METRICS:")
            lines.append(f"  History: {ts_metrics.history_days} days")
            if ts_metrics.annual_return_pct is not None:
                lines.append(f"  Annual return: {ts_metrics.annual_return_pct:+.1f}%")
            if ts_metrics.annual_volatility_pct is not None:
                lines.append(f"  Annual volatility: {ts_metrics.annual_volatility_pct:.1f}%")
            if ts_metrics.sharpe_ratio is not None:
                lines.append(f"  Sharpe ratio: {ts_metrics.sharpe_ratio:.2f}")
            if ts_metrics.max_drawdown_pct is not None:
                lines.append(f"  Max drawdown: {ts_metrics.max_drawdown_pct:.1f}%")
            if ts_metrics.current_drawdown_pct is not None:
                lines.append(f"  Current drawdown: {ts_metrics.current_drawdown_pct:.1f}%")
            if ts_metrics.correlation_clusters:
                lines.append(f"  Correlation clusters: {ts_metrics.correlation_clusters}")
            lines.append("")

        # --- Bucket drifts ---
        if bucket_drifts:
            lines.append("ALLOCATION vs IPS TARGET (5/25 band rule):")
            for bd in bucket_drifts:
                flag = " ⚠ BREACHED" if bd.breached else ""
                lines.append(
                    f"  {bd.bucket_name}: {bd.actual_pct:.1f}% "
                    f"(target {bd.target_pct:.0f}%, drift {bd.drift_pct:+.1f}pp){flag}"
                )
            lines.append("")

        # --- Alerts ---
        if alerts:
            lines.append(f"TRIGGERED ALERTS ({len(alerts)}):")
            for a in alerts:
                lines.append(f"  [{a.severity.value}] {a.category}: {a.title}")
                lines.append(f"    {a.detail}")
                lines.append(f"    Rule: {a.rule}")
            lines.append("")
        else:
            lines.append("NO ALERTS — all IPS limits respected.")
            lines.append("")

        # --- Rebalance options ---
        if rebalance_options:
            lines.append("REBALANCE OPTIONS (deterministic, pre-computed):")
            for i, opt in enumerate(rebalance_options, 1):
                lines.append(f"  Option {i}: {opt.get('name', 'N/A')}")
                lines.append(f"    {opt.get('description', '')}")
                lines.append(f"    Turnover: {opt.get('estimated_turnover_pct', 0):.1f}%")
                trades = opt.get("trades", [])
                if trades:
                    lines.append(f"    Key trades ({len(trades)}):")
                    for t in trades[:5]:
                        lines.append(
                            f"      {t.get('direction', '?')} {t.get('name', '?')}: "
                            f"{t.get('current_weight_pct', 0):.1f}% → {t.get('target_weight_pct', 0):.1f}%"
                        )
            lines.append("")

        # --- Top movers ---
        if metrics.winners:
            lines.append("Top gainers:")
            for p in metrics.winners[:5]:
                lines.append(
                    f"  {p.name} ({p.ticker}): +{p.pnl_pct:.0f}%, wt {p.weight_pct:.1f}%"
                )
        if metrics.losers:
            lines.append("Top losers:")
            for p in metrics.losers[:5]:
                lines.append(
                    f"  {p.name} ({p.ticker}): {p.pnl_pct:.0f}%, wt {p.weight_pct:.1f}%"
                )

        # --- News ---
        if news:
            lines += ["", "Recent news:"]
            for symbol, articles in news.items():
                if articles:
                    lines.append(f"  {symbol}:")
                    for a in articles[:3]:
                        lines.append(f"    - {a.get('headline', 'N/A')}")

        # --- Fundamentals ---
        if fundamentals:
            lines += ["", "Key fundamentals:"]
            for symbol, data in fundamentals.items():
                if data:
                    parts = [f"{k}: {v}" for k, v in data.items() if v is not None]
                    if parts:
                        lines.append(f"  {symbol}: {', '.join(parts)}")

        # --- Stock scores ---
        if stock_scores:
            scored = [s for s in stock_scores if s.get("fundamental_score", -1) >= 0]
            if scored:
                lines += ["", "STOCK SCORES (0-100 fundamental, valuation signal):"]
                for s in sorted(scored, key=lambda x: -x.get("weight_pct", 0))[:10]:
                    flags = []
                    if s.get("earnings_soon"):
                        flags.append("EARNINGS SOON")
                    if s.get("high_leverage"):
                        flags.append("HIGH DEBT")
                    if s.get("has_negative_growth"):
                        flags.append("NEG GROWTH")
                    if s.get("near_52w_low"):
                        flags.append("NEAR 52W LOW")
                    if s.get("near_52w_high"):
                        flags.append("NEAR 52W HIGH")
                    flag_str = f" [{', '.join(flags)}]" if flags else ""
                    lines.append(
                        f"  {s.get('name', '?')} ({s.get('ticker', '?')}): "
                        f"score={s['fundamental_score']}/100, val={s.get('valuation', '?')}, "
                        f"wt={s.get('weight_pct', 0):.1f}%, P/L={s.get('pnl_pct', 0):+.0f}%{flag_str}"
                    )

        # --- DCA signals ---
        if dca_signals:
            buy_zone = [s for s in dca_signals if s.get("signal") == "BUY_ZONE"]
            extended = [s for s in dca_signals if s.get("signal") == "EXTENDED"]
            if buy_zone or extended:
                lines += ["", "DCA / ACCUMULATION SIGNALS:"]
                for s in buy_zone[:5]:
                    sma = s.get("sma_30")
                    discount = ((s["current_price"] - sma) / sma * 100) if sma else 0
                    lines.append(
                        f"  BUY_ZONE: {s.get('name', '?')} — {discount:+.1f}% vs SMA-30"
                    )
                for s in extended[:3]:
                    sma = s.get("sma_50")
                    premium = ((s["current_price"] - sma) / sma * 100) if sma else 0
                    lines.append(
                        f"  EXTENDED: {s.get('name', '?')} — +{premium:.0f}% above SMA-50"
                    )

        # --- Deployment opportunities ---
        if opportunities:
            lines += ["", "DEPLOYMENT OPPORTUNITIES (pre-computed, cross-referenced with IPS):"]
            for opp in opportunities:
                lines.append(
                    f"  {opp.get('signal', '?')}: {opp.get('name', '?')} ({opp.get('ticker', '?')})"
                )
                lines.append(
                    f"    Bucket: {opp.get('bucket_name', '?')} "
                    f"(drift {opp.get('bucket_drift_pct', 0):+.1f}pp), "
                    f"Valuation: {opp.get('valuation', '?')}, "
                    f"Score: {opp.get('fundamental_score', -1)}/100, "
                    f"DCA: {opp.get('dca_signal', '?')}"
                )
                lines.append(f"    Reason: {opp.get('reason', 'N/A')}")

        return "\n".join(lines)
