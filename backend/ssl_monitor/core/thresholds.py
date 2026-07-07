"""Pure threshold / alert decision logic.

Fires at most one alert per run — the most urgent crossed threshold — and is
idempotent across runs via ``last_alert_threshold`` so an interval-driven
checker does not re-page on every pass.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import AlertDecision


def decide_alert(
    days_left: int | None,
    thresholds: Sequence[int],
    last_alert_threshold: int | None,
) -> AlertDecision:
    """Decide whether to alert and at which threshold.

    A smaller threshold is *more urgent* (e.g. 7 is more urgent than 30).

    Rules:
    - ``days_left is None`` (unreachable / no data): never alert; leave the
      existing ``last_alert_threshold`` untouched (the host may just be down).
    - No threshold crossed (cert healthy or renewed): never alert and reset
      ``last_alert_threshold`` to None so future crossings fire fresh.
    - Otherwise the crossed threshold is ``min(t for t in thresholds if
      days_left <= t)``. Fire only if it is strictly more urgent than what was
      last sent; on fire, persist it as the new ``last_alert_threshold``.

    An already-expired cert (days_left < 0) crosses every threshold, so it
    resolves to the most urgent one.
    """
    if days_left is None:
        return AlertDecision(should_alert=False, threshold=None, new_last_alert=last_alert_threshold)

    crossed = [t for t in thresholds if days_left <= t]
    if not crossed:
        # Healthy or renewed → clear dedup state.
        return AlertDecision(should_alert=False, threshold=None, new_last_alert=None)

    most_urgent = min(crossed)

    if last_alert_threshold is None or most_urgent < last_alert_threshold:
        return AlertDecision(
            should_alert=True, threshold=most_urgent, new_last_alert=most_urgent
        )

    # Already alerted at this level (or a more urgent one) — suppress, keep state.
    return AlertDecision(
        should_alert=False, threshold=None, new_last_alert=last_alert_threshold
    )
