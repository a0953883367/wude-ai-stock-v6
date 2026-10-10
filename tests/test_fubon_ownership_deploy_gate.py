"""Offline fixtures: fake public GETs, monotonic clock, sleep, and collector."""

from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fubon_ownership_deploy_gate as gate


SHA_A = "a" * 40
SHA_B = "b" * 40


def ref(sha=SHA_A):
    return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}}


def status(sha=SHA_A, state="success", *, context=gate.RAILWAY_CONTEXT, ident=1):
    return {"sha": sha, "state": state, "total_count": 1,
            "statuses": [{"id": ident, "context": context, "state": state}]}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, value=None, *, body=None, code=200, headers=None, chunks=None):
        self.body = body if body is not None else json.dumps(value).encode()
        self.status_code, self.headers = code, headers or {}
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size):
        if self.chunks is not None:
            yield from self.chunks
        else:
            for start in range(0, len(self.body), chunk_size):
                yield self.body[start:start + chunk_size]

    def close(self):
        self.closed = True


class Transport:
    def __init__(self, sequence, *, repeat=False, clock=None, elapsed=0):
        self.sequence = list(sequence)
        self.repeat, self.clock, self.elapsed = repeat, clock, elapsed
        self.calls, self.responses = [], []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.sequence[(len(self.calls) - 1) % len(self.sequence)] if self.repeat else self.sequence.pop(0)
        if self.clock is not None:
            self.clock.now += self.elapsed
        if isinstance(item, BaseException):
            raise item
        response = item if isinstance(item, Response) else Response(item)
        self.responses.append(response)
        return response


def snapshot(sha=SHA_A, state="success", *, ident=1):
    return [ref(sha), status(sha, state, ident=ident), ref(sha)]


class DeploymentGateTests(unittest.TestCase):
    def run_gate(self, sequence, *, repeat=False, clock=None, elapsed=0, refresh=None):
        clock = clock or Clock()
        transport = Transport(sequence, repeat=repeat, clock=clock, elapsed=elapsed)
        calls = []
        result = gate.refresh_if_deployed(get=transport, clock=clock, sleep=clock.sleep,
                                         refresh=refresh or (lambda: calls.append("refresh")))
        return result, transport, clock, calls

    def test_two_stable_bracketed_successes_call_existing_collector_once(self):
        result, transport, clock, calls = self.run_gate(snapshot() * 2)
        self.assertTrue(result.ready)
        self.assertEqual(calls, ["refresh"])
        self.assertEqual(clock.sleeps, [10])
        self.assertEqual([url for url, _ in transport.calls], [
            gate.MAIN_REF_URL,
            gate.REPOSITORY_API + "/commits/" + SHA_A + "/status?per_page=100",
            gate.MAIN_REF_URL,
        ] * 2)
        for _, kwargs in transport.calls:
            self.assertEqual(set(kwargs), {"headers", "allow_redirects", "stream", "timeout"})
            self.assertFalse(kwargs["allow_redirects"])
            self.assertTrue(kwargs["stream"])
            self.assertLessEqual(kwargs["timeout"], 5)
            self.assertGreater(kwargs["timeout"], 0)
            self.assertNotIn("Authorization", kwargs["headers"])
        self.assertTrue(all(response.closed for response in transport.responses))

    def test_pending_then_eventual_success_needs_two_new_observations(self):
        result, transport, _, calls = self.run_gate(snapshot(state="pending") + snapshot() * 2)
        self.assertTrue(result.ready)
        self.assertEqual(len(transport.calls), 9)
        self.assertEqual(calls, ["refresh"])

    def test_absent_context_then_eventual_success(self):
        missing = {"sha": SHA_A, "state": "pending", "total_count": 0, "statuses": []}
        result, _, _, calls = self.run_gate([ref(), missing, ref()] + snapshot() * 2)
        self.assertTrue(result.ready)
        self.assertEqual(calls, ["refresh"])

    def test_pending_failed_error_and_missing_never_call_provider(self):
        cases = [(snapshot(state="pending"), "railway_pending"),
                 (snapshot(state="failure"), "railway_failure"),
                 (snapshot(state="error"), "railway_error"),
                 ([ref(), status(context="other-service"), ref()], "railway_status_missing")]
        for sequence, reason in cases:
            with self.subTest(reason=reason):
                result, transport, clock, calls = self.run_gate(sequence, repeat=True)
                self.assertFalse(result.ready)
                self.assertEqual(result.reason, reason)
                self.assertFalse(calls)
                self.assertLessEqual(clock.now, 120)
                self.assertLessEqual(len(transport.calls), 36)

    def test_head_moves_after_apparent_success_does_not_release_provider(self):
        sequence = snapshot() + [ref(), status(), ref(SHA_B)]
        sequence += snapshot(SHA_B, "pending") * 10
        result, _, _, calls = self.run_gate(sequence)
        self.assertFalse(result.ready)
        self.assertFalse(calls)

    def test_head_move_skips_instead_of_recovering_to_a_new_head(self):
        sequence = snapshot() + [ref(), status(), ref(SHA_B)] + snapshot(SHA_B) * 2
        result, transport, _, calls = self.run_gate(sequence)
        self.assertEqual(result.reason, "main_moved")
        self.assertFalse(result.ready)
        self.assertEqual(len(transport.calls), 6)
        self.assertFalse(calls)

    def test_success_on_different_current_heads_skips_immediately(self):
        result, transport, _, calls = self.run_gate(snapshot() + snapshot(SHA_B) * 2)
        self.assertEqual(result.reason, "main_moved")
        self.assertFalse(result.ready)
        self.assertEqual(len(transport.calls), 4)
        self.assertFalse(calls)

    def test_head_move_while_pending_skips_without_reading_new_head_status(self):
        result, transport, _, calls = self.run_gate(snapshot(state="pending") + snapshot(SHA_B) * 2)
        self.assertEqual(result.reason, "main_moved")
        self.assertFalse(result.ready)
        self.assertEqual(len(transport.calls), 4)
        self.assertFalse(calls)

    def test_new_status_id_and_pending_reset_previous_success(self):
        for sequence in [snapshot() + snapshot(ident=2) * 2,
                         snapshot() + snapshot(state="pending") + snapshot() * 2]:
            with self.subTest(sequence=sequence):
                result, transport, _, calls = self.run_gate(sequence)
                self.assertTrue(result.ready)
                self.assertEqual(len(transport.calls), len(sequence))
                self.assertEqual(calls, ["refresh"])

    def test_non_railway_aggregate_failure_is_not_mistaken_for_railway_failure(self):
        doc = status()
        doc["statuses"].append({"id": 2, "context": "another-service", "state": "failure"})
        doc.update(state="failure", total_count=2)
        result, _, _, calls = self.run_gate([ref(), doc, ref()] * 2)
        self.assertTrue(result.ready)
        self.assertEqual(calls, ["refresh"])

    def test_duplicate_relevant_context_fails_closed(self):
        doc = status()
        doc["statuses"].append({"id": 2, "context": gate.RAILWAY_CONTEXT, "state": "pending"})
        doc["total_count"] = 2
        result, _, _, calls = self.run_gate([ref(), doc])
        self.assertEqual(result.reason, "railway_status_ambiguous")
        self.assertFalse(calls)

    def test_malformed_or_truncated_combined_status_fails_closed(self):
        docs = [status(SHA_B), {"sha": SHA_A},
                dict(status(), total_count=2), dict(status(), total_count=True),
                dict(status(), statuses={}), dict(status(), state="unknown"),
                dict(status(), statuses=[{"id": True, "context": gate.RAILWAY_CONTEXT, "state": "success"}]),
                dict(status(), total_count=101, statuses=[status()["statuses"][0]] * 101)]
        for doc in docs:
            with self.subTest(doc=doc):
                result, _, _, calls = self.run_gate([ref(), doc])
                self.assertFalse(result.ready)
                self.assertFalse(calls)

    def test_malformed_refs_and_json_fail_closed(self):
        bad_refs = [ref("../main"), ref("a" * 39), ref("A" * 40),
                    dict(ref(), ref="refs/heads/other"),
                    {"ref": "refs/heads/main", "object": {"type": "tag", "sha": SHA_A}}]
        bad_bodies = [b"not-json", b"[]", b"\xff", b'{"ref":"a","ref":"b"}']
        for value in bad_refs + [Response(body=body) for body in bad_bodies]:
            with self.subTest(value=value):
                result, transport, _, calls = self.run_gate([value])
                self.assertFalse(result.ready)
                self.assertFalse(calls)
                self.assertEqual(len(transport.calls), 1)

    def test_http_failures_and_redirects_fail_closed_without_retry(self):
        for code in (301, 302, 307, 401, 403, 404, 429, 500, 503):
            with self.subTest(code=code):
                result, transport, _, calls = self.run_gate([Response(ref(), code=code)])
                self.assertEqual(result.reason, "github_api_error")
                self.assertEqual(len(transport.calls), 1)
                self.assertFalse(calls)
                self.assertTrue(transport.responses[0].closed)

    def test_response_length_and_stream_limits_fail_closed(self):
        responses = [Response(ref(), headers={"Content-Length": str(gate.MAX_RESPONSE_BYTES + 1)}),
                     Response(ref(), headers={"Content-Length": "-1"}),
                     Response(body=b" " * (gate.MAX_RESPONSE_BYTES + 1)),
                     Response(ref(), chunks=[b" " * gate.MAX_RESPONSE_BYTES, b"x"])]
        for response in responses:
            with self.subTest(response=response):
                result, _, _, calls = self.run_gate([response])
                self.assertEqual(result.reason, "github_response_limit")
                self.assertFalse(calls)
                self.assertTrue(response.closed)

    def test_request_timeout_and_unexpected_transport_error_do_not_retry(self):
        for error, reason in [(TimeoutError("private detail"), "github_timeout"),
                              (OSError("private detail"), "github_api_error"),
                              (RuntimeError("private detail"), "gate_error")]:
            with self.subTest(error=error):
                result, transport, _, calls = self.run_gate([error])
                self.assertEqual(result.reason, reason)
                self.assertFalse(calls)
                self.assertEqual(len(transport.calls), 1)

    def test_deadline_during_request_and_at_final_ref_fails_closed(self):
        for elapsed in (120, 20):
            with self.subTest(elapsed=elapsed):
                result, transport, clock, calls = self.run_gate(snapshot() * 2, elapsed=elapsed)
                self.assertEqual(result.reason, "gate_timeout")
                self.assertFalse(calls)
                self.assertTrue(all(response.closed for response in transport.responses))
        # A too-long injected call is rejected once it returns. The production
        # SIGALRM additionally interrupts blocked transport at the hard budget.

    def test_skip_preserves_prior_artifact_and_does_not_import_collector(self):
        import io
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fubon_ownership.json"
            original = b'{"updated_at":"old","data":{"2330.TW":{"status":"available"}}}'
            path.write_bytes(original)
            output = io.StringIO()
            with patch.dict(sys.modules, {"fubon_ownership_relay": None}), patch("sys.stdout", output):
                clock = Clock()
                result = gate.refresh_if_deployed(get=Transport(snapshot(state="failure")),
                                                 clock=clock, sleep=clock.sleep)
            self.assertFalse(result.ready)
            self.assertEqual(path.read_bytes(), original)
            self.assertIn("gate_skipped artifact_not_refreshed", output.getvalue())
            self.assertIn("prior_artifact_preserved freshness=stale_or_unknown", output.getvalue())
            self.assertNotIn("private detail", output.getvalue())

    def test_no_existing_artifact_is_created_on_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            before = list(Path(directory).iterdir())
            result, _, _, _ = self.run_gate(snapshot(state="error"))
            self.assertFalse(result.ready)
            self.assertEqual(list(Path(directory).iterdir()), before)

    def test_real_entrypoint_uses_no_token_auth_preserves_proxy_tls_and_restores_alarm(self):
        clock = Clock()
        transport = Transport(snapshot() * 2)
        session = SimpleNamespace(trust_env=True, auth=None, get=transport,
                                  proxies={"https": "http://configured-proxy:8080"},
                                  verify="/configured/ca-bundle.pem", headers={})

        @contextmanager
        def session_context():
            yield session

        timer_calls, handlers = [], []
        fake_signal = SimpleNamespace(ITIMER_REAL=0, SIGALRM=14,
                                      getitimer=lambda _: (0, 0), getsignal=lambda _: "old",
                                      signal=lambda *args: handlers.append(args),
                                      setitimer=lambda *args: timer_calls.append(args))
        refresh_calls = []

        def refresh():
            self.assertEqual(timer_calls[-1], (0, 0))
            self.assertEqual(handlers[-1], (14, "old"))
            refresh_calls.append(1)

        requests = SimpleNamespace(Session=session_context, auth=SimpleNamespace(AuthBase=object))
        with patch.dict(sys.modules, {"requests": requests}), patch.object(gate, "signal", fake_signal):
            result = gate.refresh_if_deployed(clock=clock, sleep=clock.sleep, refresh=refresh)
        self.assertTrue(result.ready)
        self.assertTrue(session.trust_env)
        self.assertEqual(session.proxies, {"https": "http://configured-proxy:8080"})
        self.assertEqual(session.verify, "/configured/ca-bundle.pem")
        self.assertTrue(session.auth)
        request = SimpleNamespace(headers={"Authorization": "fixture-not-a-real-token"})
        self.assertIs(session.auth(request), request)
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Authorization", session.headers)
        self.assertEqual(timer_calls, [(0, 120), (0, 0)])
        self.assertEqual(refresh_calls, [1])

    def test_hard_wall_timer_expiry_and_unavailable_timer_skip(self):
        clock = Clock()
        handlers, timers = [], []
        fake_signal = SimpleNamespace(ITIMER_REAL=0, SIGALRM=14,
                                      getitimer=lambda _: (0, 0), getsignal=lambda _: "old",
                                      signal=lambda *args: handlers.append(args),
                                      setitimer=lambda *args: timers.append(args))

        def blocked_get(*args, **kwargs):
            handlers[0][1](14, None)

        @contextmanager
        def session_context():
            yield SimpleNamespace(get=blocked_get)

        calls = []
        requests = SimpleNamespace(Session=session_context, auth=SimpleNamespace(AuthBase=object))
        with patch.dict(sys.modules, {"requests": requests}), patch.object(gate, "signal", fake_signal):
            result = gate.refresh_if_deployed(clock=clock, sleep=clock.sleep, refresh=lambda: calls.append(1))
        self.assertEqual(result.reason, "gate_timeout")
        self.assertFalse(calls)
        self.assertEqual(timers, [(0, 120), (0, 0)])
        with patch.dict(sys.modules, {"requests": requests}), patch.object(gate, "signal", SimpleNamespace()):
            result = gate.refresh_if_deployed(refresh=lambda: calls.append(1))
        self.assertEqual(result.reason, "gate_timer_unavailable")
        self.assertFalse(calls)

    def test_provider_exception_is_not_retried_or_hidden(self):
        calls = []

        def fail():
            calls.append(1)
            raise RuntimeError("collector failed")

        with self.assertRaisesRegex(RuntimeError, "collector failed"):
            self.run_gate(snapshot() * 2, refresh=fail)
        self.assertEqual(calls, [1])

    def test_workflow_preserves_publication_order_optional_timeout_and_environment(self):
        text = (Path(__file__).parents[1] / ".github/workflows/stock-briefing.yml").read_text()
        ownership = "name: Refresh read-only Fubon ownership supplement"
        block = text.split(ownership, 1)[1].split("      - name:", 1)[0]
        self.assertIn("run: python fubon_ownership_deploy_gate.py", block)
        self.assertIn("continue-on-error: true", block)
        self.assertIn("timeout-minutes: 10", block)
        self.assertIn("WUDE_LIVE_API_BASE: https://wude-ai-stock-v6-production.up.railway.app", block)
        self.assertLess(text.index("name: Keep reports and recent archive"), text.index(ownership))
        self.assertLess(text.index("name: Safely advance prospective shadow registry from latest main"), text.index(ownership))
        self.assertLess(text.index(ownership), text.index("name: Publish optional ownership supplement"))


if __name__ == "__main__":
    unittest.main()
