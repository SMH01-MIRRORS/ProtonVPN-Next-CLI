"""Tests for AmneziaWG handshake verification.

Mirrors the Android client's ConnectionVerificationModeTest so both platforms
agree on the log predicates, the deadline and the reconnect behaviour.
"""

import os
import tempfile
import unittest

from pvpn_cli.handshake import (
    DEFAULT_MODE,
    DEFAULT_TIMEOUT_SECONDS,
    HEALTH_PENDING,
    HEALTH_STALLED,
    HEALTH_VERIFIED,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    MODE_DISABLED,
    MODE_HANDSHAKE,
    HandshakeCounters,
    HandshakeTracker,
    count_handshake_events,
    handshake_succeeded,
    is_handshake_attempt,
    is_handshake_success,
    normalize_mode,
    normalize_timeout,
    wait_for_handshake,
)

SUCCESS_LINE = "DEBUG: (awg0) 2026/08/04 12:30:11 peer(HkvZ…jaQ8) - Received handshake response"
ATTEMPT_LINE = "DEBUG: (awg0) 2026/08/04 12:30:11 peer(HkvZ…jaQ8) - Sending handshake initiation"
WORKER_LINE = "DEBUG: (awg0) 2026/08/04 12:30:11 Routine: handshake worker 7 - started"


class LogPredicateTest(unittest.TestCase):
    def test_success_line_is_detected(self):
        self.assertTrue(is_handshake_success(SUCCESS_LINE))
        self.assertTrue(is_handshake_success("Handshake response received"))

    def test_attempt_line_is_detected(self):
        self.assertTrue(is_handshake_attempt(ATTEMPT_LINE))
        self.assertFalse(is_handshake_success(ATTEMPT_LINE))

    def test_worker_startup_is_not_a_handshake(self):
        self.assertFalse(is_handshake_success(WORKER_LINE))
        self.assertFalse(is_handshake_attempt(WORKER_LINE))

    def test_empty_input_is_safe(self):
        self.assertFalse(is_handshake_success(""))
        self.assertFalse(is_handshake_attempt(None))


class SettingsNormalizationTest(unittest.TestCase):
    def test_only_two_modes_are_supported(self):
        self.assertEqual(normalize_mode("disabled"), MODE_DISABLED)
        self.assertEqual(normalize_mode("handshake"), MODE_HANDSHAKE)

    def test_unknown_mode_falls_back_to_the_default(self):
        self.assertEqual(normalize_mode("aggressive"), DEFAULT_MODE)
        self.assertEqual(normalize_mode(None), DEFAULT_MODE)

    def test_mode_synonyms(self):
        self.assertEqual(normalize_mode("off"), MODE_DISABLED)
        self.assertEqual(normalize_mode("handshake_only"), MODE_HANDSHAKE)

    def test_timeout_defaults_to_five_seconds(self):
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 5)
        self.assertEqual(normalize_timeout(None), 5)
        self.assertEqual(normalize_timeout("nonsense"), 5)

    def test_timeout_is_clamped_like_android(self):
        self.assertEqual(normalize_timeout(1), MIN_TIMEOUT_SECONDS)
        self.assertEqual(normalize_timeout(120), MAX_TIMEOUT_SECONDS)
        self.assertEqual(normalize_timeout("8"), 8)


class LogCountingTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log_path = os.path.join(self.directory.name, "awg.log")

    def write(self, *lines):
        with open(self.log_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def test_missing_log_yields_zero_counters(self):
        counters = count_handshake_events(os.path.join(self.directory.name, "absent.log"))
        self.assertEqual(counters, HandshakeCounters(0, 0))

    def test_counts_attempts_and_successes(self):
        self.write(WORKER_LINE, ATTEMPT_LINE, SUCCESS_LINE, ATTEMPT_LINE)
        counters = count_handshake_events(self.log_path)
        self.assertEqual(counters.attempts, 2)
        self.assertEqual(counters.successes, 1)
        self.assertTrue(counters.renegotiating)

    def test_success_requires_a_response(self):
        self.write(ATTEMPT_LINE)
        self.assertFalse(handshake_succeeded(self.log_path))
        self.write(ATTEMPT_LINE, SUCCESS_LINE)
        self.assertTrue(handshake_succeeded(self.log_path))

    def test_wait_returns_immediately_on_success(self):
        self.write(ATTEMPT_LINE, SUCCESS_LINE)
        slept = []
        self.assertTrue(
            wait_for_handshake(self.log_path, 5, sleep=slept.append, monotonic=lambda: 0.0)
        )
        self.assertEqual(slept, [])

    def test_wait_times_out_without_a_response(self):
        self.write(ATTEMPT_LINE)
        clock = {"now": 0.0}

        def monotonic():
            return clock["now"]

        def sleep(seconds):
            clock["now"] += seconds

        self.assertFalse(
            wait_for_handshake(self.log_path, 5, sleep=sleep, monotonic=monotonic)
        )
        self.assertGreaterEqual(clock["now"], 5)

    def test_wait_ignores_handshakes_from_a_previous_tunnel(self):
        self.write(ATTEMPT_LINE, SUCCESS_LINE)
        clock = {"now": 0.0}
        self.assertFalse(
            wait_for_handshake(
                self.log_path,
                5,
                baseline_successes=1,
                sleep=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
                monotonic=lambda: clock["now"],
            )
        )

    def test_wait_accepts_a_truncated_log_as_a_new_tunnel(self):
        self.write(ATTEMPT_LINE, SUCCESS_LINE)
        self.assertTrue(
            wait_for_handshake(
                self.log_path, 5, baseline_successes=7, sleep=lambda _s: None, monotonic=lambda: 0.0
            )
        )


class HandshakeTrackerTest(unittest.TestCase):
    def test_pending_until_the_first_response(self):
        tracker = HandshakeTracker(5)
        self.assertEqual(tracker.update(HandshakeCounters(1, 0), now=0.0), HEALTH_PENDING)
        self.assertFalse(tracker.verified)

    def test_verified_after_a_response(self):
        tracker = HandshakeTracker(5)
        self.assertEqual(tracker.update(HandshakeCounters(1, 1), now=0.0), HEALTH_VERIFIED)
        self.assertTrue(tracker.verified)

    def test_rekey_does_not_break_a_verified_tunnel(self):
        tracker = HandshakeTracker(5)
        tracker.update(HandshakeCounters(1, 1), now=0.0)
        # AmneziaWG rekeys every two minutes; a brief pending handshake is normal.
        self.assertEqual(tracker.update(HandshakeCounters(2, 1), now=120.0), HEALTH_VERIFIED)
        self.assertEqual(tracker.update(HandshakeCounters(2, 2), now=120.4), HEALTH_VERIFIED)

    def test_unanswered_handshake_stalls_after_the_deadline(self):
        tracker = HandshakeTracker(5)
        tracker.update(HandshakeCounters(1, 1), now=0.0)
        # A DPI reset makes the engine retry a handshake that nobody answers.
        self.assertEqual(tracker.update(HandshakeCounters(2, 1), now=10.0), HEALTH_VERIFIED)
        self.assertEqual(tracker.update(HandshakeCounters(3, 1), now=14.9), HEALTH_VERIFIED)
        self.assertEqual(tracker.update(HandshakeCounters(3, 1), now=15.0), HEALTH_STALLED)

    def test_custom_timeout_is_honoured(self):
        tracker = HandshakeTracker(30)
        tracker.update(HandshakeCounters(1, 1), now=0.0)
        tracker.update(HandshakeCounters(2, 1), now=1.0)
        self.assertEqual(tracker.update(HandshakeCounters(2, 1), now=20.0), HEALTH_VERIFIED)
        self.assertEqual(tracker.update(HandshakeCounters(2, 1), now=31.0), HEALTH_STALLED)

    def test_truncated_log_resets_the_tracker(self):
        tracker = HandshakeTracker(5)
        tracker.update(HandshakeCounters(4, 4), now=0.0)
        self.assertEqual(tracker.update(HandshakeCounters(1, 0), now=1.0), HEALTH_PENDING)
        self.assertFalse(tracker.verified)


if __name__ == "__main__":
    unittest.main()
