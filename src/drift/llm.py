"""Local LLM backends as a DRIFT capability.

This module is why the framework exists, but the core does not depend on it:
:mod:`drift.node` never imports this file. An LLM is just a capability with an
unusually interesting scorer.

What makes it interesting: for inference, *where the weights already are*
dominates everything else. Loading a 7B model into VRAM costs tens of seconds;
generating a few hundred tokens costs a fraction of that. So a machine with the
model already resident should outbid a faster machine that would have to load
it — which is exactly the opposite of what a CPU-load-based score would say.

Cross-backend naming is handled here too. Ollama calls it ``gemma3:4b``,
LM Studio calls it ``google/gemma-3-4b``. A request for ``gemma3`` must match
both, or a mixed Hive can never route a job.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

from .scoring import ScoreContext, clamp, default_scorer

OLLAMA_URL = "http://localhost:11434"
LMSTUDIO_URL = "http://localhost:1234/v1"

# A resident model is worth more than any plausible resource advantage. The
# resource score spans 0-100; 70 keeps a warm-but-busy node ahead of a
# cold-and-idle one without making load irrelevant between two warm nodes.
RESIDENT_BONUS = 70
POLL_INTERVAL = 5.0


def normalise(model: str) -> str:
    """Reduce a model name to something comparable across backends."""
    return "".join(ch for ch in model.lower() if ch.isalnum())


def matches(requested: str, candidate: str) -> bool:
    """True if `candidate` plausibly satisfies `requested`.

    Substring on the normalised form, both directions: ``gemma3`` matches
    ``google/gemma-3-4b``, and ``gemma3:4b`` matches a catalogue entry of
    ``gemma3``.
    """
    req, cand = normalise(requested), normalise(candidate)
    if not req or not cand:
        return False
    return req in cand or cand in req


def _get(url: str, timeout: float = 4.0):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 600.0):
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class LocalLLM:
    """Wraps an Ollama or LM Studio server running on this machine."""

    def __init__(self, backend: str = "auto", url: str | None = None, poll: bool = True):
        self.backend = backend if backend != "auto" else self._detect()
        self.url = (url or (OLLAMA_URL if self.backend == "ollama" else LMSTUDIO_URL)).rstrip("/")
        self.installed: list[str] = []  # on disk
        self.resident: list[str] = []  # currently in VRAM
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.refresh()
        if poll and self.backend != "mock":
            threading.Thread(target=self._poll_loop, daemon=True).start()

    # ---------------- discovery ----------------
    @staticmethod
    def _detect() -> str:
        for backend, probe in (("ollama", f"{OLLAMA_URL}/api/tags"), ("lmstudio", f"{LMSTUDIO_URL}/models")):
            try:
                _get(probe, timeout=1.5)
                return backend
            except Exception:
                continue
        return "mock"

    def refresh(self) -> None:
        installed, resident = [], []
        try:
            if self.backend == "ollama":
                installed = [m["name"] for m in _get(f"{self.url}/api/tags").get("models", [])]
                resident = [m["name"] for m in _get(f"{self.url}/api/ps").get("models", [])]
            elif self.backend == "lmstudio":
                # LM Studio only lists models it has loaded, so installed and
                # resident are the same set.
                installed = [m["id"] for m in _get(f"{self.url}/models").get("data", [])]
                resident = list(installed)
            elif self.backend == "mock":
                installed = resident = ["mock-model"]
        except Exception:
            # A backend that is down means "cannot serve", not a crash. The
            # empty lists make score() return 0 and this node drops out of
            # auctions until the backend comes back.
            pass
        with self._lock:
            self.installed, self.resident = installed, resident

    def _poll_loop(self) -> None:
        while not self._stop.wait(POLL_INTERVAL):
            self.refresh()

    def stop(self) -> None:
        self._stop.set()

    # ---------------- matching ----------------
    def resolve(self, requested: str | None) -> tuple[str | None, str]:
        """Return `(model_name, state)` where state is 'resident'|'installed'|'none'."""
        with self._lock:
            resident, installed = list(self.resident), list(self.installed)

        if not requested or requested.lower() == "any":
            if resident:
                return resident[0], "resident"
            if installed:
                return installed[0], "installed"
            return None, "none"

        for candidate in resident:
            if matches(requested, candidate):
                return candidate, "resident"
        for candidate in installed:
            if matches(requested, candidate):
                return candidate, "installed"
        return None, "none"

    # ---------------- scoring ----------------
    def score(self, ctx: ScoreContext) -> int:
        """Resource score plus a large bonus for having the weights loaded.

        Note the return value can exceed 100. Scores are ordinal — the auction
        only compares them — so the ceiling would throw away exactly the
        information that decides the routing.
        """
        model, state = self.resolve(ctx.extra.get("model"))
        if state == "none":
            return 0  # no matching weights: stay out of the auction entirely
        base = default_scorer(ctx)
        return max(1, base + (RESIDENT_BONUS if state == "resident" else 0))

    def detail(self, ctx: ScoreContext) -> dict:
        model, state = self.resolve(ctx.extra.get("model"))
        return {
            "model": model,
            "state": state,
            "backend": self.backend,
            "gpu": ctx.metrics.get("gpu"),
            "active": ctx.active_tasks,
        }

    # ---------------- generation ----------------
    def generate(self, prompt: str, model: str | None = None, system: str | None = None,
                 temperature: float = 0.7, timeout: float = 600.0) -> dict:
        resolved, state = self.resolve(model)
        if not resolved:
            return {"error": f"no model matching '{model}' on this node"}

        started = time.time()
        if self.backend == "ollama":
            body = {"model": resolved, "prompt": prompt, "stream": False,
                    "options": {"temperature": temperature}}
            if system:
                body["system"] = system
            text = _post(f"{self.url}/api/generate", body, timeout).get("response", "")
        elif self.backend == "lmstudio":
            messages = ([{"role": "system", "content": system}] if system else []) + [
                {"role": "user", "content": prompt}
            ]
            body = {"model": resolved, "messages": messages, "temperature": temperature, "stream": False}
            data = _post(f"{self.url}/chat/completions", body, timeout)
            text = data["choices"][0]["message"]["content"]
        else:
            text = f"[mock] {prompt[:120]}"

        return {
            "text": text.strip(),
            "model": resolved,
            "state_at_bid": state,
            "backend": self.backend,
            "seconds": round(time.time() - started, 2),
        }

    # ---------------- wiring ----------------
    def attach(self, node, cap: str = "llm", cost: int = 3) -> "LocalLLM":
        """Register this backend as a capability on `node`."""

        def handler(payload: dict) -> dict:
            return self.generate(
                prompt=payload.get("prompt", ""),
                model=payload.get("model"),
                system=payload.get("system"),
                temperature=payload.get("temperature", 0.7),
            )

        node.register(cap, handler, cost=cost, scorer=self.score, detail=self.detail)
        node.log(
            f"llm capability '{cap}' ready · backend={self.backend} · "
            f"resident={self.resident or '-'} installed={len(self.installed)}",
            level="success",
        )
        return self


__all__ = ["LocalLLM", "matches", "normalise", "RESIDENT_BONUS", "clamp"]
