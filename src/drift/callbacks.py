"""Callback signature checks, done once at wiring time.

drift is a library: the scorer, the logger and the capability test are all
supplied by the host application. When drift's own call shape changes, those
callbacks keep the old one, and every call raises `TypeError`.

That failure used to be invisible. The auction wraps the scorer in
``try/except`` — correctly, because a broken sensor must not take the whole
mesh down — so a wrong signature looked exactly like a node that chose not to
bid. Nobody bid, ``elect()`` returned None, work silently stopped being
distributed, and the only symptom was one warn line per auction.

So the check moved to the moment the callback is handed over. There, a wrong
signature is unambiguous and cannot be mistaken for a policy decision: it raises
immediately, at start-up, naming the callback and the shape drift will call it
with. A crash on boot is a far better failure than a mesh that quietly runs
everything on one machine.

Callables that cannot be introspected (C functions, some builtins) are left
alone — an unknown signature is not evidence of a wrong one.
"""

from __future__ import annotations

import inspect


def check_callback(fn, name: str, *args, **kwargs) -> None:
    """Raise `TypeError` now if `fn` could not accept ``fn(*args, **kwargs)``.

    The arguments are only used for their shape; they are never called with.
    Pass whatever placeholder values are convenient.
    """
    if fn is None:
        return
    if not callable(fn):
        raise TypeError(f"drift: {name} is not callable (got {type(fn).__name__})")
    try:
        signature = inspect.signature(fn)
    except (ValueError, TypeError):
        return  # not introspectable — say nothing rather than guess
    try:
        signature.bind(*args, **kwargs)
    except TypeError as e:
        shape = ", ".join(
            [f"<{a}>" for a in args] + [f"{k}=<{v}>" for k, v in kwargs.items()]
        )
        raise TypeError(
            f"drift calls {name} as {name}({shape}) but the callback you passed "
            f"is {name}{signature} — {e}"
        ) from None
