"""Token storage for Socket.IO sessions."""

from __future__ import annotations

# In-memory storage for client tokens (sid -> token)
_client_tokens: dict[str, str] = {}


def store_token(sid: str, token: str) -> None:
    """Store backend token for a Socket.IO session ID."""
    _client_tokens[sid] = token


def get_token(sid: str) -> str | None:
    """Get stored backend token for a Socket.IO session ID."""
    return _client_tokens.get(sid)


def remove_token(sid: str) -> None:
    """Remove stored token for a Socket.IO session ID."""
    _client_tokens.pop(sid, None)

