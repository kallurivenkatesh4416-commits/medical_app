# Slice 18 Emergency Load Report

- Run at (UTC): `2026-05-22T07:08:59.619405+00:00`
- Target: `http://127.0.0.1:8001`
- Run label: `local Postgres medapp_load_test + stub providers`
- Requests: `100`
- Concurrency: `10`
- Provider path: local/stub run; confirm backend env before release use.
- Command: `BASE_URL=http://127.0.0.1:8001 REQUESTS=100 CONCURRENCY=10 python scripts/load_emergency.py`

| Metric | Result |
|---|---:|
| HTTP 200 | 100 |
| Non-200 / transport failures | 0 |
| Status counts | `{200: 100}` |
| p50 | 179.82 ms |
| p95 | 591.38 ms |
| p99 | 995.62 ms |

## Assertions

- PASS every request returned HTTP 200.
- PASS p50 < 500 ms.
- PASS p95 < 2 s.
- PASS p99 < 5 s.

Overall: **PASS**.

## Notes

- Payloads use synthetic `fall` symptom codes and synthetic location text.
- This run proves the local/stub API path only. Provider delivery latency needs staging telemetry once live Twilio and FCM keys are present.
