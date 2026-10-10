"""Keep optional ownership reads behind a bounded, public Railway deploy gate.

This only observes GitHub's latest main ref and its Railway commit status. Two
stable observations reduce an obvious deployment race; they are not an atomic
lock, process-health check, SDK/session check, or the Stage B readiness gate.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import re
import signal
import time

REPOSITORY_API = "https://api.github.com/repos/a0953883367/wude-ai-stock-v6"
MAIN_REF_URL = REPOSITORY_API + "/git/ref/heads/main"
RAILWAY_CONTEXT = "focused-courtesy - wude-ai-stock-v6"
MAX_WAIT_SECONDS = 120
POLL_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 5
MAX_OBSERVATIONS = 12
MAX_RESPONSE_BYTES = 262144
MAX_STATUS_ROWS = 100
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
HEADERS = {
    "Accept": "application/vnd.github+json",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "User-Agent": "wude-ownership-deploy-gate",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GateClosed(Exception):
    """A fixed, safe reason for skipping this optional refresh."""


@dataclass(frozen=True)
class GateResult:
    ready: bool
    reason: str


def _remaining(deadline, clock):
    remaining = deadline - clock()
    if remaining <= 0:
        raise GateClosed("gate_timeout")
    return remaining


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GateClosed("github_malformed_response")
        result[key] = value
    return result


def _read_json(url, *, get, deadline, clock):
    # The caller constructs URLs from fixed constants and a validated SHA only.
    response = None
    try:
        response = get(url, headers=HEADERS, allow_redirects=False, stream=True,
                       timeout=min(REQUEST_TIMEOUT_SECONDS, _remaining(deadline, clock)))
        _remaining(deadline, clock)
        if response.status_code != 200:
            raise GateClosed("github_api_error")
        length = response.headers.get("Content-Length")
        if length is not None and (not str(length).isdigit() or int(length) > MAX_RESPONSE_BYTES):
            raise GateClosed("github_response_limit")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=4096):
            _remaining(deadline, clock)
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise GateClosed("github_response_limit")
            body.extend(chunk)
        _remaining(deadline, clock)
        result = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(result, dict):
            raise GateClosed("github_malformed_response")
        return result
    except TimeoutError:
        raise GateClosed("github_timeout") from None
    except OSError:
        raise GateClosed("github_api_error") from None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GateClosed("github_malformed_response") from None
    finally:
        if response is not None:
            response.close()


def _main_sha(**kwargs):
    doc = _read_json(MAIN_REF_URL, **kwargs)
    obj = doc.get("object")
    if (doc.get("ref") != "refs/heads/main" or not isinstance(obj, dict)
            or obj.get("type") != "commit" or not isinstance(obj.get("sha"), str)
            or not SHA_PATTERN.fullmatch(obj["sha"])):
        raise GateClosed("github_malformed_ref")
    return obj["sha"]


def _railway_status(sha, **kwargs):
    doc = _read_json(REPOSITORY_API + "/commits/" + sha + "/status?per_page=100", **kwargs)
    rows, count = doc.get("statuses"), doc.get("total_count")
    if (doc.get("sha") != sha or not isinstance(rows, list)
            or type(count) is not int or count != len(rows) or count > MAX_STATUS_ROWS
            or doc.get("state") not in {"pending", "success", "failure", "error"}):
        raise GateClosed("github_malformed_status")
    matches = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("context"), str)
                or not row["context"] or len(row["context"]) > 256
                or row.get("state") not in {"pending", "success", "failure", "error"}
                or type(row.get("id")) is not int or row["id"] <= 0):
            raise GateClosed("github_malformed_status")
        if row["context"] == RAILWAY_CONTEXT:
            matches.append(row)
    # The combined-status API supplies the latest status for each context.
    # An absent or duplicate exact context must never count as success.
    if len(matches) > 1:
        raise GateClosed("railway_status_ambiguous")
    return matches[0] if matches else None


def wait_for_deployment(*, get, clock=time.monotonic, sleep=time.sleep):
    """Pure injectable observation loop, bounded by time and request count."""
    deadline = clock() + MAX_WAIT_SECONDS
    observed_sha = None
    previous = None
    reason = "railway_status_missing"
    try:
        for attempt in range(MAX_OBSERVATIONS):
            options = {"get": get, "deadline": deadline, "clock": clock}
            sha = _main_sha(**options)
            if observed_sha is not None and sha != observed_sha:
                return GateResult(False, "main_moved")
            observed_sha = sha
            status = _railway_status(sha, **options)
            latest_sha = _main_sha(**options)
            if latest_sha != sha:
                return GateResult(False, "main_moved")
            elif status is None:
                previous, reason = None, "railway_status_missing"
            elif status["state"] in {"failure", "error"}:
                return GateResult(False, "railway_" + status["state"])
            elif status["state"] == "pending":
                previous, reason = None, "railway_pending"
            else:
                observation = (sha, status["id"])
                _remaining(deadline, clock)
                if observation == previous:
                    return GateResult(True, "railway_success_stable")
                previous, reason = observation, "railway_success_unconfirmed"
            if attempt + 1 < MAX_OBSERVATIONS:
                sleep(min(POLL_SECONDS, _remaining(deadline, clock)))
        return GateResult(False, reason)
    except GateClosed as exc:
        return GateResult(False, str(exc))


@contextmanager
def _wall_time_limit():
    # The workflow uses ubuntu-latest. SIGALRM also bounds DNS and slow reads,
    # for which requests' connect/read timeouts alone are not a total deadline.
    # Fail closed on unsupported runtimes or an already-owned alarm.
    if not hasattr(signal, "setitimer") or signal.getitimer(signal.ITIMER_REAL)[0]:
        raise GateClosed("gate_timer_unavailable")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        raise GateClosed("gate_timeout")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, MAX_WAIT_SECONDS)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def refresh_if_deployed(*, get=None, clock=time.monotonic, sleep=time.sleep, refresh=None):
    """Import/call the existing collector once, only after the public gate."""
    try:
        if get is None:
            import requests

            class PublicOnlyAuth(requests.auth.AuthBase):
                def __call__(self, request):
                    request.headers.pop("Authorization", None)
                    return request

            with _wall_time_limit(), requests.Session() as session:
                # An explicit auth object suppresses requests' netrc lookup.
                # Keep the configured proxy and TLS environment unchanged.
                session.auth = PublicOnlyAuth()
                result = wait_for_deployment(get=session.get, clock=clock, sleep=sleep)
        else:
            result = wait_for_deployment(get=get, clock=clock, sleep=sleep)
    except GateClosed as exc:
        result = GateResult(False, str(exc))
    except Exception:
        # Never expose response bodies/exception details or turn uncertainty
        # into an ownership call, including unexpected transport/schema errors.
        result = GateResult(False, "gate_error")
    if not result.ready:
        print("Fubon ownership: gate_skipped artifact_not_refreshed "
              "prior_artifact_preserved freshness=stale_or_unknown reason=" + result.reason)
        return result
    print("Fubon ownership: deployment_gate_passed; starting existing refresh once")
    if refresh is None:
        from fubon_ownership_relay import refresh
    refresh()
    return result


if __name__ == "__main__":
    refresh_if_deployed()
