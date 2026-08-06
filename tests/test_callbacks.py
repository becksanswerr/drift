"""Callback signatures are checked when they are handed over, not when called.

The failure this guards against: drift changed the shape it calls host
callbacks with, a host kept the old shape, and every call raised `TypeError`
inside a `try/except` or a daemon thread. Nothing crashed. The auction simply
stopped producing bids, and distributed work quietly became local work.

Both directions matter here. It must reject a callback drift cannot call — and
it must accept every legitimate way of writing one (`*args`, extra defaults,
keyword-only, a bound method, a callable object), or the check itself becomes
the outage.
"""

import pytest

from drift.auction import Auctioneer
from drift.callbacks import check_callback
from drift.mesh import MeshNode
from test_auction import FakeMesh


# ---------------- the checker itself ----------------
def test_wrong_arity_is_rejected():
    with pytest.raises(TypeError):
        check_callback(lambda cap: 0, "score_fn", "cap", {})


def test_error_names_the_callback_and_both_shapes():
    with pytest.raises(TypeError) as e:
        check_callback(lambda cap: 0, "score_fn", "cap", {})
    text = str(e.value)
    assert "score_fn" in text
    assert "(<cap>, <{}>)" in text or "score_fn(cap)" in text


def test_non_callable_is_rejected():
    with pytest.raises(TypeError):
        check_callback(42, "score_fn", "cap", {})


def test_none_is_allowed():
    """Optional callbacks stay optional."""
    check_callback(None, "bid_detail", "cap", {})


@pytest.mark.parametrize(
    "fn",
    [
        lambda cap, hint: 0,
        lambda cap, hint=None: 0,
        lambda *args: 0,
        lambda *args, **kwargs: 0,
        lambda cap, hint=None, extra=1: 0,
    ],
)
def test_legitimate_shapes_are_accepted(fn):
    check_callback(fn, "score_fn", "cap", {})


def test_bound_methods_and_callable_objects_are_accepted():
    class Host:
        def score(self, cap, hint=None):
            return 0

        def __call__(self, cap, hint=None):
            return 0

    host = Host()
    check_callback(host.score, "score_fn", "cap", {})
    check_callback(host, "score_fn", "cap", {})


def test_uninspectable_callables_are_left_alone():
    """An unknown signature is not evidence of a wrong one.

    `zip` is a C type whose signature `inspect` refuses to describe. It stands in
    for the C extensions and wrapped callables a host might legitimately pass.
    """
    check_callback(zip, "log", "message", level="info", source="drift")


# ---------------- wired into the real classes ----------------
def test_auction_rejects_a_scorer_with_the_old_signature():
    with pytest.raises(TypeError) as e:
        Auctioneer(FakeMesh(), can_serve=lambda c: True, score_fn=lambda cap: 50)
    assert "score_fn" in str(e.value)


def test_auction_rejects_a_logger_that_takes_arguments_positionally():
    """Exactly the Valori outage: a logger shaped `log(level, source, msg)`."""
    with pytest.raises(TypeError):
        Auctioneer(
            FakeMesh(),
            can_serve=lambda c: True,
            score_fn=lambda cap, hint=None: 50,
            log=lambda level, source, msg: None,
        )


def test_auction_accepts_correct_callbacks():
    Auctioneer(
        FakeMesh(),
        can_serve=lambda c: True,
        score_fn=lambda cap, hint=None: 50,
        bid_detail=lambda cap, hint=None: {},
        log=lambda msg, level="info", source=None: None,
    )


def test_mesh_rejects_an_on_log_with_the_old_signature():
    with pytest.raises(TypeError) as e:
        MeshNode(name="alpha", caps=["llm"], discovery="static",
                 discovery_port=47399, on_log=lambda level, source, msg: None)
    assert "on_log" in str(e.value)


def test_mesh_accepts_a_correct_on_log():
    node = MeshNode(name="alpha", caps=["llm"], discovery="static",
                    discovery_port=47398,
                    on_log=lambda msg, level="info", source=None: None)
    node.sock.close()
