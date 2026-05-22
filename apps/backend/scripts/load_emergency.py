r"""Concurrent emergency-alert smoke/load runner for Slice 18.

Prerequisites:
1. Run a local backend with stub notification providers.
2. Complete resident onboarding once and export the resulting access token:
   ``$env:RESIDENT_ACCESS_TOKEN = "<token>"``.
3. Run this script from ``apps/backend``:
   ``.\.venv\Scripts\python.exe scripts\load_emergency.py``.

The report is written to ``docs/slice18-load-report.md`` unless REPORT_PATH
overrides it. Payloads stay synthetic and idempotency keys are unique.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, request

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
ACCESS_TOKEN = os.getenv("RESIDENT_ACCESS_TOKEN")
REQUESTS = int(os.getenv("REQUESTS", "100"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "10"))
RUN_LABEL = os.getenv("RUN_LABEL", "local/stub")
ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = Path(os.getenv("REPORT_PATH", ROOT / "docs" / "slice18-load-report.md"))


@dataclass(frozen=True)
class Result:
    status: int
    latency_ms: float
    error: str | None = None


def _post_alert(index: int) -> Result:
    payload = json.dumps(
        {
            "symptom_codes": ["fall"],
            "location_text": f"slice18-load-{index}",
        }
    ).encode()
    req = request.Request(
        f"{BASE_URL}/api/v1/emergency/alerts",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"slice18-load-{uuid.uuid4().hex}",
        },
    )
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=15) as response:
            response.read()
            return Result(
                status=response.status,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
    except error.HTTPError as exc:
        exc.read()
        return Result(
            status=exc.code,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=f"HTTP {exc.code}",
        )
    except OSError as exc:
        return Result(
            status=0,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=exc.__class__.__name__,
        )


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0
    if percentile == 50:
        return statistics.median(values)
    position = max(0, round((percentile / 100) * len(values) + 0.5) - 1)
    return sorted(values)[min(position, len(values) - 1)]


def _write_report(
    *,
    results: list[Result],
    started_at: datetime,
    p50: float,
    p95: float,
    p99: float,
    failures: list[str],
) -> None:
    passed = len(failures) == 0
    ok = [result for result in results if result.status == 200]
    status_counts: dict[int, int] = {}
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# Slice 18 Emergency Load Report",
                "",
                f"- Run at (UTC): `{started_at.isoformat()}`",
                f"- Target: `{BASE_URL}`",
                f"- Run label: `{RUN_LABEL}`",
                f"- Requests: `{REQUESTS}`",
                f"- Concurrency: `{CONCURRENCY}`",
                "- Provider path: local/stub run; confirm backend env before release use.",
                (
                    "- Command: "
                    f"`BASE_URL={BASE_URL} REQUESTS={REQUESTS} "
                    f"CONCURRENCY={CONCURRENCY} python scripts/load_emergency.py`"
                ),
                "",
                "| Metric | Result |",
                "|---|---:|",
                f"| HTTP 200 | {len(ok)} |",
                f"| Non-200 / transport failures | {len(results) - len(ok)} |",
                f"| Status counts | `{status_counts}` |",
                f"| p50 | {p50:.2f} ms |",
                f"| p95 | {p95:.2f} ms |",
                f"| p99 | {p99:.2f} ms |",
                "",
                "## Assertions",
                "",
                f"- {'PASS' if len(ok) == REQUESTS else 'FAIL'} every request returned HTTP 200.",
                f"- {'PASS' if p50 < 500 else 'FAIL'} p50 < 500 ms.",
                f"- {'PASS' if p95 < 2000 else 'FAIL'} p95 < 2 s.",
                f"- {'PASS' if p99 < 5000 else 'FAIL'} p99 < 5 s.",
                "",
                f"Overall: **{'PASS' if passed else 'FAIL'}**.",
                "",
                "## Notes",
                "",
                "- Payloads use synthetic `fall` symptom codes and synthetic location text.",
                "- This run proves the local/stub API path only. Provider delivery latency "
                "needs staging telemetry once live Twilio and FCM keys are present.",
                *([f"- Failure: {failure}" for failure in failures] if failures else []),
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    if not ACCESS_TOKEN:
        raise SystemExit("RESIDENT_ACCESS_TOKEN is required.")
    started_at = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(_post_alert, range(REQUESTS)))

    ok_latencies = [result.latency_ms for result in results if result.status == 200]
    p50 = _percentile(ok_latencies, 50)
    p95 = _percentile(ok_latencies, 95)
    p99 = _percentile(ok_latencies, 99)
    failures: list[str] = []
    if len(ok_latencies) != REQUESTS:
        failures.append("one or more alert requests did not return HTTP 200")
    if p50 >= 500:
        failures.append("p50 latency was >= 500 ms")
    if p95 >= 2000:
        failures.append("p95 latency was >= 2 s")
    if p99 >= 5000:
        failures.append("p99 latency was >= 5 s")

    _write_report(
        results=results,
        started_at=started_at,
        p50=p50,
        p95=p95,
        p99=p99,
        failures=failures,
    )
    print(f"wrote {REPORT_PATH}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
