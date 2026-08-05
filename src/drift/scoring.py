"""Scoring: how a node decides what it is worth for a given task.

The paper calls this the *Cognitive Budget*. A node starts from a full budget
and spends it against whatever is already occupying the machine — CPU, RAM,
GPU, and the tasks it is currently running. The highest remaining budget wins
the auction.

Two properties matter more than the exact numbers:

1. **Load balancing beats raw power.** A node that is already executing a task
   must bid noticeably lower, or the fastest machine in the house wins every
   auction, queues everything behind itself, and the rest of the Hive idles.
   That is the failure mode the parallel penalty exists to prevent.

2. **Locality of state beats locality of hardware.** For LLM work the dominant
   cost is loading weights into VRAM, not the tokens themselves. A node with
   the model already resident should outbid a faster but cold node — see
   :mod:`drift.llm`.

Everything here is a plain function of a :class:`ScoreContext`. Write your own
and hand it to ``Node(scorer=...)`` if your workload disagrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Weights are relative penalties applied to a 100-point budget.
#
# GPU is weighted highest because a busy GPU serialises inference; CPU is a
# tie-breaker; RAM matters only when a model spills out of VRAM into it.
#
# The parallel penalty is 40 and not, say, 20, because it has to beat the
# resource gap between two ordinary machines. Metrics rarely differ by more
# than ~30 points in practice, so a smaller penalty leaves the strongest node
# winning every auction while already busy — which is the exact bottleneck the
# penalty exists to break. Above ~50 the opposite failure appears: a machine
# running one trivial task drops below a saturated one. See test_scoring.py.
GPU_WEIGHT = 0.35
CPU_WEIGHT = 0.30
RAM_WEIGHT = 0.15
PARALLEL_PENALTY = 40
COST_PENALTY = 5  # per declared cost unit of the capability


@dataclass
class ScoreContext:
    """Everything the scorer is allowed to look at."""

    cap: str
    metrics: dict = field(default_factory=dict)  # {"cpu": int, "ram": int, "gpu": int|None}
    active_tasks: int = 0
    cost: int = 1  # declared cost of the capability, see Node.capability(cost=)
    caps: set = field(default_factory=set)
    extra: dict = field(default_factory=dict)  # capability-specific hints (e.g. model name)


def default_scorer(ctx: ScoreContext) -> int:
    """Resource-aware score in the 0-100 range. 0 means "do not bid"."""
    cpu = _pct(ctx.metrics.get("cpu"))
    ram = _pct(ctx.metrics.get("ram"))
    gpu = ctx.metrics.get("gpu")

    score = 100.0
    score -= CPU_WEIGHT * cpu
    score -= RAM_WEIGHT * ram
    if gpu is not None:
        score -= GPU_WEIGHT * _pct(gpu)
    score -= PARALLEL_PENALTY * ctx.active_tasks
    score -= COST_PENALTY * max(0, ctx.cost - 1)

    return clamp(score)


def explain(ctx: ScoreContext) -> dict:
    """The `why` payload that travels with a bid. Keep it small and flat."""
    return {
        "cpu": ctx.metrics.get("cpu"),
        "ram": ctx.metrics.get("ram"),
        "gpu": ctx.metrics.get("gpu"),
        "active": ctx.active_tasks,
        "cost": ctx.cost,
    }


def clamp(score: float) -> int:
    return max(0, min(100, int(round(score))))


def _pct(value) -> float:
    """Coerce a metric to a 0-100 float. Missing metrics count as idle.

    Treating an unknown value as 0 rather than 100 is intentional: a machine
    without `psutil` installed should still be able to take work, otherwise a
    minimal install silently never wins an auction.
    """
    if value is None:
        return 0.0
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
