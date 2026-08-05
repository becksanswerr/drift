import pytest

from drift.llm import RESIDENT_BONUS, LocalLLM, matches, normalise
from drift.scoring import ScoreContext


@pytest.fixture
def llm():
    backend = LocalLLM(backend="mock", poll=False)
    backend.installed = ["llama3:8b", "gemma3:4b"]
    backend.resident = ["gemma3:4b"]
    return backend


def ctx(model=None, cpu=0, ram=0, gpu=0, active=0):
    return ScoreContext(cap="llm", metrics={"cpu": cpu, "ram": ram, "gpu": gpu},
                        active_tasks=active, cost=3, extra={"model": model} if model else {})


# ---------------- naming ----------------
def test_normalise_strips_backend_punctuation():
    assert normalise("gemma-3:4b") == "gemma34b"
    assert normalise("google/gemma-3-4b") == "googlegemma34b"


@pytest.mark.parametrize("requested, candidate", [
    ("gemma3", "gemma3:4b"),               # ollama tag
    ("gemma3", "google/gemma-3-4b"),       # lm studio id
    ("Gemma3", "gemma3:4b"),               # case
    ("gemma3:4b", "gemma3"),               # request more specific than catalogue
])
def test_cross_backend_matching(requested, candidate):
    assert matches(requested, candidate)


@pytest.mark.parametrize("requested, candidate", [
    ("gemma3", "llama3:8b"),
    ("qwen", "gemma3:4b"),
    ("", "gemma3:4b"),
])
def test_non_matches(requested, candidate):
    assert not matches(requested, candidate)


# ---------------- resolution ----------------
def test_resolve_prefers_resident_weights(llm):
    assert llm.resolve("gemma3") == ("gemma3:4b", "resident")


def test_resolve_finds_installed_but_cold(llm):
    assert llm.resolve("llama3") == ("llama3:8b", "installed")


def test_resolve_any_takes_whatever_is_loaded(llm):
    assert llm.resolve("any") == ("gemma3:4b", "resident")
    assert llm.resolve(None) == ("gemma3:4b", "resident")


def test_resolve_unknown_model(llm):
    assert llm.resolve("qwen3") == (None, "none")


# ---------------- scoring ----------------
def test_node_without_the_model_stays_out_of_the_auction(llm):
    assert llm.score(ctx("qwen3")) == 0


def test_no_models_at_all_means_no_bid():
    empty = LocalLLM(backend="mock", poll=False)
    empty.installed = empty.resident = []
    assert empty.score(ctx("gemma3")) == 0


def test_warm_weights_beat_a_faster_cold_machine(llm):
    """The routing decision this whole module exists for.

    Loading a model costs tens of seconds; a few points of CPU headroom does
    not. A busy node holding the weights should still win.
    """
    warm_and_loaded = llm.score(ctx("gemma3", cpu=70, gpu=60))
    cold_and_idle = llm.score(ctx("llama3", cpu=0, gpu=0))
    assert warm_and_loaded > cold_and_idle


def test_between_two_warm_nodes_load_decides(llm):
    assert llm.score(ctx("gemma3", gpu=10)) > llm.score(ctx("gemma3", gpu=90))


def test_resident_bonus_is_the_difference(llm):
    warm = llm.score(ctx("gemma3"))
    cold = llm.score(ctx("llama3"))
    assert warm - cold == RESIDENT_BONUS


def test_detail_explains_the_bid(llm):
    detail = llm.detail(ctx("gemma3"))
    assert detail["model"] == "gemma3:4b"
    assert detail["state"] == "resident"
    assert detail["backend"] == "mock"


# ---------------- wiring ----------------
def test_attach_registers_a_capability(llm):
    from drift import Node

    node = Node(name="t", discovery="static", discovery_port=47399, http_port=8899)
    try:
        llm.attach(node, cap="llm")
        assert "llm" in node.capabilities
        assert node.capabilities["llm"].scorer == llm.score
        result = node.call("llm", {"prompt": "hello"})
        assert result["model"] == "mock-model" or "mock" in result["text"]
    finally:
        node.stop()
