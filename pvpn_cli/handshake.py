"""AmneziaWG handshake verification.

The userspace engine (amneziawg-go) logs every handshake initiation and every
handshake response to ``awg.log``.  Those two lines are the only trustworthy
proof that the peer answered us: the ``awg0`` interface appears even when a DPI
box silently drops the tunnel, and routes are installed before any packet is
exchanged.

This module owns the log parsing, the user settings and the health state machine
so that the CLI, the local API and the GUI all agree on what "connected" means.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

# Verification modes exposed in the UI.  "disabled" keeps the historical
# behaviour (interface presence only), "handshake" requires a real handshake.
MODE_DISABLED = "disabled"
MODE_HANDSHAKE = "handshake"
SUPPORTED_MODES = (MODE_DISABLED, MODE_HANDSHAKE)
DEFAULT_MODE = MODE_HANDSHAKE

# Handshake deadline, mirroring the Android client's reconnect timeout.
DEFAULT_TIMEOUT_SECONDS = 5
MIN_TIMEOUT_SECONDS = 3
MAX_TIMEOUT_SECONDS = 30

SETTING_MODE = "connection_verification_mode"
SETTING_TIMEOUT = "handshake_timeout_seconds"

# Reconnect attempts performed by a single connect command before giving up.
MAX_CONNECT_ATTEMPTS = 3

# Health states reported by HandshakeTracker.
HEALTH_VERIFIED = "verified"
HEALTH_PENDING = "pending"
HEALTH_STALLED = "stalled"

# Only the last part of awg.log is relevant, and the file grows for as long as
# the tunnel lives.  Reading a bounded tail keeps the watcher cheap.
_MAX_TAIL_BYTES = 256 * 1024


def normalize_mode(value) -> str:
    """Return a supported verification mode for any stored or posted value."""
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in SUPPORTED_MODES:
            return candidate
        # Accept the obvious synonyms the GUI and scripts may send.
        if candidate in ("off", "none", "false", "0"):
            return MODE_DISABLED
        if candidate in ("on", "true", "1", "handshake_only", "relaxed"):
            return MODE_HANDSHAKE
    return DEFAULT_MODE


def normalize_timeout(value) -> int:
    """Clamp the handshake deadline into the supported range."""
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, seconds))


def is_handshake_success(line: str) -> bool:
    """True only for the engine line proving the peer answered our handshake."""
    normalized = (line or "").lower()
    return "received handshake response" in normalized or "handshake response received" in normalized


def is_handshake_attempt(line: str) -> bool:
    """True when the engine starts a new handshake, e.g. after a DPI reset."""
    return "sending handshake initiation" in (line or "").lower()


@dataclass(frozen=True)
class HandshakeCounters:
    """How many handshakes were started and answered in the current log."""

    attempts: int = 0
    successes: int = 0

    @property
    def renegotiating(self) -> bool:
        """A handshake is in flight and has not been answered yet."""
        return self.attempts > self.successes


def read_log_tail(log_path: str, max_bytes: int = _MAX_TAIL_BYTES) -> str:
    """Read the tail of the engine log, tolerating rotation and missing files."""
    try:
        with open(log_path, "rb") as handle:
            try:
                size = os.fstat(handle.fileno()).st_size
                if size > max_bytes:
                    handle.seek(size - max_bytes)
            except OSError:
                pass
            return handle.read().decode("utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return ""


def count_handshake_events(log_path: str) -> HandshakeCounters:
    """Count handshake initiations and responses in the engine log."""
    attempts = 0
    successes = 0
    for line in read_log_tail(log_path).splitlines():
        if is_handshake_success(line):
            successes += 1
        elif is_handshake_attempt(line):
            attempts += 1
    return HandshakeCounters(attempts=attempts, successes=successes)


def handshake_succeeded(log_path: str, baseline_successes: int = 0) -> bool:
    """True when a new handshake response arrived after the given baseline."""
    return count_handshake_events(log_path).successes > baseline_successes


def reset_log(log_path: str) -> bool:
    """Empty the engine log so only the next tunnel's handshakes are counted.

    The engine truncates ``awg.log`` every time it starts, so a baseline counted
    from the previous tunnel cannot be compared with the new one: when the old
    log ended with exactly as many handshake responses as the new tunnel
    answers, the fresh response looks like the old one and a working tunnel is
    reported as dead.  Starting from an empty log removes that ambiguity.

    Returns True when the log is empty afterwards.  An engine that is still
    shutting down can keep the file open on Windows, so callers have to fall
    back to a counted baseline when this returns False.
    """
    try:
        with open(log_path, "w", encoding="utf-8"):
            pass
        return True
    except OSError:
        return False


def read_settings(db=None) -> tuple[str, int]:
    """Return the (mode, timeout_seconds) pair configured by the user."""
    if db is None:
        from pvpn_cli.database import Database

        db = Database()
    mode = normalize_mode(db.get_setting(SETTING_MODE, DEFAULT_MODE))
    timeout = normalize_timeout(db.get_setting(SETTING_TIMEOUT, str(DEFAULT_TIMEOUT_SECONDS)))
    return mode, timeout


def wait_for_handshake(
    log_path: str,
    timeout: float,
    baseline_successes: int = 0,
    poll_interval: float = 0.2,
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> bool:
    """Wait until the peer answers a handshake or the deadline expires."""
    deadline = monotonic() + max(0.0, float(timeout))
    baseline = max(0, int(baseline_successes))
    while True:
        counters = count_handshake_events(log_path)
        if counters.successes < baseline:
            # The engine restarted and truncated its log, so every handshake
            # response now in the file belongs to the new tunnel.
            baseline = 0
        if counters.successes > baseline:
            return True
        if monotonic() >= deadline:
            return False
        sleep(poll_interval)


class HandshakeTracker:
    """Track tunnel health across handshake renegotiations.

    AmneziaWG rekeys every two minutes and also restarts a handshake whenever a
    DPI box drops the session.  A pending handshake is therefore normal; only a
    handshake that stays unanswered for longer than the configured deadline
    means the tunnel is dead and has to be rebuilt.
    """

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self._last_successes = 0
        self._pending_since = None
        self._verified = False

    @property
    def verified(self) -> bool:
        """True once the peer answered at least one handshake."""
        return self._verified

    def reset(self) -> None:
        """Forget all state, e.g. after a disconnect or a new engine log."""
        self._last_successes = 0
        self._pending_since = None
        self._verified = False

    def update(self, counters: HandshakeCounters, now: float) -> str:
        """Fold new log counters into the health state and return that state."""
        if counters.successes < self._last_successes:
            # The engine truncated its log, so this is a brand new tunnel.
            self.reset()

        if counters.successes > self._last_successes:
            self._last_successes = counters.successes
            self._verified = True
            self._pending_since = None

        if counters.renegotiating:
            if self._pending_since is None:
                self._pending_since = now
            elif now - self._pending_since >= self.timeout_seconds:
                return HEALTH_STALLED
            return HEALTH_VERIFIED if self._verified else HEALTH_PENDING

        self._pending_since = None
        return HEALTH_VERIFIED if self._verified else HEALTH_PENDING

    def poll(self, log_path: str, now=None) -> str:
        """Read the engine log once and return the resulting health state."""
        moment = time.monotonic() if now is None else now
        return self.update(count_handshake_events(log_path), moment)
