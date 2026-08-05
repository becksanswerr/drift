"""Shared-secret protection for the control plane and the data plane.

Trust model — read this before deploying:

DRIFT assumes the Hive runs on a network you already trust (a LAN, or a
WireGuard/Tailscale overlay). The secret is a *membership* check, not a
defence against an attacker who is already inside that network. Concretely:

- Discovery/control datagrams are encrypted with Fernet (AES-128-CBC + HMAC),
  so a passive listener on the LAN cannot read task payloads or peer metadata,
  and a node without the secret cannot join the Hive.
- Data-plane HTTP calls carry a bearer token derived from the same secret.
  That token is constant, so it authenticates the *sender* but does not stop a
  replay by someone who captured the header. Put the data plane on a trusted
  network; do not expose port 8700 to the internet.
- Without a secret, everything runs in the clear. That is a legitimate choice
  for a home lab and a bad one anywhere else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

try:
    from cryptography.fernet import Fernet, InvalidToken

    _HAVE_CRYPTO = True
except ImportError:  # pragma: no cover - exercised only on bare installs
    Fernet = None
    InvalidToken = Exception
    _HAVE_CRYPTO = False


class SecretRequired(RuntimeError):
    """Raised when a secret is configured but `cryptography` is not installed."""


class NetworkSecurity:
    """Symmetric envelope for datagrams plus a token for HTTP calls."""

    def __init__(self, secret: str | None = None):
        self.enabled = bool(secret)
        self.fernet = None
        self._token = None

        if not secret:
            return
        if not _HAVE_CRYPTO:
            raise SecretRequired(
                "A secret was provided but `cryptography` is missing. "
                "Install it with: pip install 'drift-ai[secure]'"
            )

        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(digest))
        # A separate derivation for HTTP so the bearer token can never be
        # replayed as a datagram key, and vice versa.
        self._token = hashlib.sha256(b"drift-http:" + digest).hexdigest()

    # ---------------- datagrams ----------------
    def encrypt(self, payload: str) -> bytes:
        if not self.fernet:
            return payload.encode("utf-8")
        return self.fernet.encrypt(payload.encode("utf-8"))

    def decrypt(self, data: bytes) -> str | None:
        """Returns None for anything this node cannot authenticate.

        Callers treat None as "not ours" and drop the packet silently. That is
        deliberate: on a busy LAN the socket sees unrelated broadcast traffic,
        and logging every foreign datagram would bury the real events.
        """
        if not self.fernet:
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return None
        try:
            return self.fernet.decrypt(data).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError):
            return None

    # ---------------- http ----------------
    @property
    def token(self) -> str | None:
        return self._token

    def auth_header(self) -> dict:
        return {"X-Drift-Auth": self._token} if self._token else {}

    def check_token(self, presented: str | None) -> bool:
        if not self._token:
            return True
        if not presented:
            return False
        return hmac.compare_digest(self._token, presented)
