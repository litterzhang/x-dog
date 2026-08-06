"""Cost calculation utilities."""

from __future__ import annotations

from dataclasses import replace

from xdog.ai.types import CostBreakdown, Model, Usage

_PER_MILLION = 1_000_000.0


def calculate_cost(model: Model, usage: Usage) -> CostBreakdown:
    """Compute the cost breakdown for *usage* on *model*.

    For per-token pricing (``model.cost.output > 0``), computes dollar
    cost per million tokens.  For per-request pricing (Copilot premium
    multiplier in ``model.cost.input``, ``output == 0``), sets ``total``
    to the multiplier value directly.
    """
    c = model.cost
    if c.output > 0 or c.cache_read > 0 or c.cache_write > 0:
        # Per-token pricing
        input_cost = (usage.input / _PER_MILLION) * c.input
        output_cost = (usage.output / _PER_MILLION) * c.output
        cache_read_cost = (usage.cache_read / _PER_MILLION) * c.cache_read
        cache_write_cost = (usage.cache_write / _PER_MILLION) * c.cache_write
        total = input_cost + output_cost + cache_read_cost + cache_write_cost
        return CostBreakdown(
            input=input_cost, output=output_cost,
            cache_read=cache_read_cost, cache_write=cache_write_cost,
            total=total,
        )
    # Per-request pricing (premium multiplier)
    return CostBreakdown(total=c.input)


def usage_with_cost(model: Model, usage: Usage) -> Usage:
    """Return a new Usage with the cost field populated."""
    return replace(usage, cost=calculate_cost(model, usage))
