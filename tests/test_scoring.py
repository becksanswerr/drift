from drift.scoring import PARALLEL_PENALTY, ScoreContext, default_scorer, explain


def ctx(cpu=0, ram=0, gpu=0, active=0, cost=1):
    return ScoreContext(cap="x", metrics={"cpu": cpu, "ram": ram, "gpu": gpu},
                        active_tasks=active, cost=cost)


def test_idle_node_scores_full():
    assert default_scorer(ctx()) == 100


def test_load_lowers_the_bid():
    assert default_scorer(ctx(cpu=90, gpu=90)) < default_scorer(ctx(cpu=10, gpu=10))


def test_busy_node_loses_to_idle_node_even_when_faster():
    """The load-balancing property, stated as a test.

    A machine already running a task must not win the next one just because
    its raw metrics look better; otherwise the strongest node hoards work.
    """
    strong_but_busy = default_scorer(ctx(cpu=5, ram=5, gpu=0, active=1))
    weak_but_idle = default_scorer(ctx(cpu=40, ram=60, gpu=30, active=0))
    assert weak_but_idle > strong_but_busy


def test_parallel_penalty_is_linear():
    assert default_scorer(ctx(active=0)) - default_scorer(ctx(active=1)) == PARALLEL_PENALTY


def test_expensive_capability_bids_slightly_lower():
    assert default_scorer(ctx(cost=3)) < default_scorer(ctx(cost=1))


def test_score_never_leaves_the_range():
    assert default_scorer(ctx(cpu=100, ram=100, gpu=100, active=5, cost=9)) == 0
    assert default_scorer(ctx()) <= 100


def test_missing_metrics_count_as_idle():
    """A node without psutil should still be able to win work."""
    bare = ScoreContext(cap="x", metrics={"cpu": None, "ram": None, "gpu": None})
    assert default_scorer(bare) == 100


def test_explain_reports_what_drove_the_score():
    detail = explain(ctx(cpu=50, active=2, cost=3))
    assert detail == {"cpu": 50, "ram": 0, "gpu": 0, "active": 2, "cost": 3}
