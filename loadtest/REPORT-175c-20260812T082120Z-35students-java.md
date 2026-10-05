# Judge0 load test — 175 simultaneous requests

- **Target**: `http://65.0.29.135:2358` (Judge0 1.13.1)
- **Run at**: 2026-08-12T08:13:35+00:00  ·  duration 465.0 s
- **Concurrency**: 175 simultaneous requests per scenario (released together via a thread barrier)
- **Workload**: language_id 62, trivial `a+b`, `cpu_time_limit=2.0` `wall_time_limit=5.0` `memory_limit=128000`, `base64_encoded=true`
- **Client**: pop-os, python 3.10.12, keep-alive pool of 175

Server config at test time: `max_queue_size=500`, `max_submission_batch_size=20`, `enable_wait_result=True`, `submission_cache_duration=1.0`.

## Headline numbers

| Scenario | Requests | Wall clock | Throughput | Errors | Correct verdicts |
|---|---:|---:|---:|---:|---:|
| **async** | 175 create + 462 poll | 228.20 s | 0.77 sub/s | 0 | 175/175 (100.0%) |
| **batch** | 9 create + 474 poll | 226.75 s | 0.77 sub/s | 0 | 175/175 (100.0%) |

## Latency distributions (ms)

| Measurement | n | min | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| async: create (POST) | 175 | 138.6 | 2,304.9 | 4,141.7 | 4,399.6 | 4,464.8 | 4,518.8 |
| async: batch poll (GET) | 462 | 45.6 | 67.3 | 103.9 | 174.3 | 333.7 | 492.9 |
| async: time to terminal (from t0) | 175 | 5,803.6 | 117,434.2 | 207,312.1 | 217,890.7 | 226,145.5 | 228,200.3 |
| async: server exec (finished_at−created_at) | 175 | 3,856.0 | 114,054.0 | 201,009.0 | 212,993.0 | 220,787.0 | 221,965.0 |
| batch: create (POST) | 9 | 530.8 | 1,489.7 | 2,067.6 | 2,067.6 | 2,067.6 | 2,067.6 |
| batch: batch poll (GET) | 474 | 44.2 | 66.8 | 103.8 | 169.3 | 204.7 | 508.5 |
| batch: time to terminal (from t0) | 175 | 4,309.9 | 117,981.6 | 205,973.3 | 218,386.6 | 224,698.3 | 226,751.8 |
| batch: server exec (finished_at−created_at) | 175 | 3,259.0 | 114,484.0 | 202,898.0 | 215,012.0 | 222,436.0 | 223,858.0 |

## Scenario `async` — 175 simultaneous POST /submissions (wait=false)

- Enqueued **175/175** submissions in **4.570 s** (38.3 POST/s)
- Drained all tokens in **228.20 s** over 96 poll cycles / 462 poll requests
- Effective throughput: **0.77 submissions/sec**
- Queue depth (`GET /workers[0].size`): 0 before → 171 right after submit → peak 171 during the run
- Statuses: `{"3": 175}` (175 Accepted, 0 never reached terminal)
- Reported CPU time per submission: p50 0.160 s, max 0.245 s

**Control-plane probe during this scenario** (`GET /workers` every 250 ms): 748 probes, 0 failed, latency p50 41.0 ms / p95 140.9 ms / max 2,204.7 ms. Peak queue depth 171.

Queue-depth series (t:size): 0.101s:0, 1.069s:38, 1.412s:49, 1.809s:62, 2.153s:76, 2.504s:90, 2.804s:103, 3.194s:113, 3.758s:132, 4.277s:153, 4.59s:170, 5.016s:171, 5.499s:171, 5.811s:171, 6.247s:171, 6.552s:171, 6.961s:171, 7.275s:171, 7.951s:171, 8.25s:169, 8.54s:169, 8.835s:169, 9.121s:169, 9.42s:169, 9.71s:169, 10.004s:169, 10.294s:167, 10.581s:167, 10.867s:167, 11.154s:167, 11.444s:167, 11.735s:167, 12.03s:167, 12.32s:167, 12.622s:167, 12.907s:167, 13.205s:165, 13.494s:165, 13.784s:165, 14.076s:165

## Scenario `batch` — 175 submissions via 9 batch POSTs

- Enqueued **175/175** submissions in **2.075 s** (84.3 POST/s)
- Drained all tokens in **226.75 s** over 97 poll cycles / 474 poll requests
- Effective throughput: **0.77 submissions/sec**
- Queue depth (`GET /workers[0].size`): 0 before → 173 right after submit → peak 173 during the run
- Statuses: `{"3": 175}` (175 Accepted, 0 never reached terminal)
- Reported CPU time per submission: p50 0.160 s, max 0.271 s

**Control-plane probe during this scenario** (`GET /workers` every 250 ms): 734 probes, 0 failed, latency p50 45.7 ms / p95 157.7 ms / max 1,106.7 ms. Peak queue depth 173.

Queue-depth series (t:size): 0.077s:0, 0.38s:16, 0.685s:42, 0.993s:76, 1.299s:94, 1.594s:130, 1.895s:148, 2.198s:173, 2.537s:173, 2.886s:173, 3.196s:173, 3.489s:172, 3.841s:171, 4.147s:171, 4.442s:171, 4.745s:171, 5.04s:171, 5.342s:171, 5.634s:171, 5.934s:171, 6.23s:171, 6.522s:170, 6.846s:169, 7.156s:169, 7.457s:169, 7.76s:169, 8.052s:169, 8.347s:169, 8.638s:169, 8.935s:169, 9.228s:167, 9.548s:167, 9.845s:167, 10.274s:167, 10.573s:167, 10.993s:167, 11.286s:167, 11.68s:167, 11.971s:166, 12.412s:166

## Raw data

Full per-request samples: `results-175c-20260812T082120Z-35students-java.json`

