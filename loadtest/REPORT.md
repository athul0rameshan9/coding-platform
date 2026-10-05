# Judge0 load test — 40 simultaneous requests

**Target** `http://65.0.29.135:2358` · Judge0 **1.13.1** · 2 vCPU / 913 MiB, `COUNT=2` workers,
10 Rails request threads
**Run** 2026-08-07 16:37–16:42 UTC, from `pop-os` over the public internet (plain HTTP)
**Harness** [judge0_loadtest.py](judge0_loadtest.py) · driver [run.sh](run.sh)
**Runs** 3 × 40-concurrency: two full repeats on Python 3.8.1 (id 71) and one on Java 13 (id 62)

Every scenario releases its 40 requests through a `threading.Barrier`, so they leave the client
together rather than ramping up as a thread pool warms. All requests use
`base64_encoded=true` and send limits explicitly (`cpu_time_limit=2.0`, `wall_time_limit=5.0`,
`memory_limit=128000`). Workload is a trivial `a+b` with `expected_output` set, so the numbers
measure the judge's dispatch capacity rather than candidate code.

---

## 1. Verdict

**The box handled 40 simultaneous requests on every path with zero errors** — no 503 "queue is
full", no 429, no timeouts, no dropped connections, and every graded submission returned the
right verdict (40/40 Accepted in each submission scenario of each run). But 40 simultaneous
requests is *not* 40 simultaneous
candidates: the queued path drains at **~2.8 submissions/sec**, so 40 submissions take **14
seconds** and a realistic "40 candidates submit a 10-test problem at once" is **400 submissions
≈ 2.5 minutes**.

| | Result |
|---|---|
| Failure rate at 40 concurrent, all paths | **0 failures / 670 requests** across 3 runs |
| Sustained throughput, Python | **2.74 – 2.90 sub/s** (reproducible across repeats) |
| Sustained throughput, Java | **0.75 sub/s** — half the 1.4 sub/s the API doc estimates |
| Time to drain 40 simultaneous Python submissions | **13.8 – 14.6 s** |
| Time to drain 40 simultaneous Java submissions | **53.6 s** |
| Peak queue depth reached | **38 of `MAX_QUEUE_SIZE=500`** (7.6 %) |
| Binding constraint | **2 judge workers on 2 vCPU** — not the queue, not the HTTP layer |

---

## 2. Headline numbers per scenario

Run 1 (Python) / Run 2 (Python, repeat) / Run 3 (Java). All figures are for 40 simultaneous
requests.

| Scenario | What it hits | Wall clock | Throughput | Errors | Verdicts |
|---|---|---:|---:|---:|---:|
| `baseline` — 3 serial `wait=true` | reference, idle box | — | — | 0 | 3/3 ✅ |
| `async` — 40 × `POST /submissions?wait=false` | 10 Rails threads → Resque → 2 workers | 13.99 s / 14.58 s / **53.64 s** | 2.86 / 2.74 / **0.75** sub/s | 0 | 40/40 ✅ |
| `batch` — 2 × `POST /submissions/batch` (20 each) | same, 2 HTTP requests | 14.07 s / 13.80 s | 2.84 / 2.90 sub/s | 0 | 40/40 ✅ |
| `wait` — 40 × `POST /submissions?wait=true` | 10 Rails threads, **inline execution** | 4.09 s / 4.05 s | **9.78 / 9.87 sub/s** | 0 | 40/40 ✅ |
| `read` — 40 × `GET /submissions/:token` | pure read, ~1 s response cache | 1.02 s | 39.3 req/s | 0 | n/a |

Reference latency on the idle box: serial `wait=true` **p50 205 ms, max 252 ms** (Python).
That is the yardstick every number below is measured against.

---

## 3. Latency distributions (ms)

### Run 1 — Python 3.8.1, 40 concurrent

| Measurement | n | min | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline serial `wait=true` (idle) | 3 | 198 | 205 | 252 | 252 | 252 | 252 |
| `async` create POST | 40 | 186 | **1,091** | 2,439 | 2,772 | 2,927 | 2,927 |
| `async` batch-poll GET | 13 | 82 | 106 | 120 | 122 | 122 | 122 |
| `async` time to terminal (from t0) | 40 | 3,720 | 8,182 | 13,990 | 13,990 | 13,990 | 13,990 |
| `async` server exec (`finished_at−created_at`) | 40 | 748 | 6,523 | 10,583 | 10,733 | 11,542 | 11,542 |
| `batch` create POST (20 submissions each) | 2 | 440 | 440 | 618 | 618 | 618 | 618 |
| `batch` batch-poll GET | 15 | 87 | 99 | 207 | 240 | 240 | 240 |
| `batch` time to terminal | 40 | 1,663 | 7,785 | 11,977 | 14,068 | 14,068 | 14,068 |
| `wait=true` × 40 | 40 | 781 | **2,318** | 3,875 | 4,058 | 4,064 | 4,064 |
| `read` GET `/submissions/:token` | 40 | 103 | 339 | 756 | 917 | 993 | 993 |

### Run 2 — Python 3.8.1, repeat (variance check)

| Measurement | n | min | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| `async` create POST | 40 | 103 | 646 | 1,095 | 1,228 |
| `async` time to terminal | 40 | 2,068 | 8,182 | 14,578 | 14,578 |
| `batch` create POST | 2 | 324 | 324 | 517 | 517 |
| `wait=true` × 40 | 40 | 331 | 2,230 | 4,012 | 4,038 |

p50 time-to-terminal reproduced to **8,182 ms in both runs**; throughput within 6 %. Create-POST
latency is the one noisy metric (p50 1,091 ms vs 646 ms) — it tracks client-side connection setup
for 40 fresh sockets, not server work.

### Run 3 — Java 13, 40 concurrent

| Measurement | n | min | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| create POST | 40 | 98 | 559 | 1,231 | 1,520 |
| batch-poll GET | 40 | 88 | 114 | 303 | 643 |
| time to terminal | 40 | 4,629 | **28,039** | 49,448 | **53,639** |
| server exec | 40 | 3,311 | 25,795 | 48,139 | 50,632 |
| reported CPU `time` | 40 | 0.109 s | 0.183 s | 0.207 s | 0.222 s |

Note the gap: 0.18 s of **CPU** per Java submission but 28 s of **wall** waiting — that is queue
time behind 2 workers, not execution cost.

---

## 4. What each path actually tells you

### 4.1 Batch is ~2.5× cheaper to *enqueue*, identical to *drain*

| | 40 individual POSTs | 2 batch POSTs of 20 |
|---|---:|---:|
| Enqueue window | 2.95 s / 1.24 s | **0.62 s / 0.52 s** |
| Enqueue rate | 13.6 / 32.4 POST/s | **64.5 / 77.1 POST/s** |
| Create p99 | 2,927 ms | 618 ms |
| HTTP requests to create | 40 | **2** |
| Drain time | 13.99 s / 14.58 s | 14.07 s / 13.80 s |
| Throughput | 2.86 / 2.74 sub/s | 2.84 / 2.90 sub/s |

Batch collapses the entire submit burst into two requests that finish in half a second, and it
keeps create-POST p99 at 618 ms instead of 2.9 s. It changes nothing downstream, because the
2 workers are the bottleneck either way. **Use batch** — the win is that the candidate's "Submit"
click returns immediately and the API server never sees a 40-deep connection burst.

### 4.2 `wait=true` is *faster* at 40 concurrent — and that is exactly the trap

40 concurrent `wait=true` calls finished in **4.05 s at 9.87 sub/s**, 3.5× the queued path's
throughput. The reason is structural: `wait=true` runs `IsolateJob.perform_now` inline in the
Rails request thread, so the work spreads over **10 Rails threads** instead of **2 Resque
workers**. It looks like free capacity. It is not:

- **Per-request latency degrades 11×**: 205 ms idle → **p50 2,318 ms**, max 4,064 ms. Max/min
  spread within the burst was **5.2×**, the signature of head-of-line queueing.
- **The control plane goes with it.** A background probe hitting `GET /workers` every 250 ms
  measured **p90 1,354 ms / max 1,354 ms** during the `wait=true` burst, versus **p50 79 ms,
  p95 178 ms** during the async burst. The probe only completed **7 samples in 4.1 s** where the
  250 ms interval predicts ~16 — it was starved by candidate code holding Rails threads.
- **Backpressure disappears.** Queue depth read **0 throughout** the `wait=true` runs — inline
  jobs never enter Resque, so `MAX_QUEUE_SIZE` and `GET /workers[0].size` are both blind to them.
- 40 trivial 0.2 s programs were survivable. 40 programs at the default `wall_time_limit=10` would
  hold all 10 threads for 10 s and take the whole API — including result polls and health checks —
  offline.

This measurement confirms §5 of [JUDGE0_API.md](../docs/JUDGE0_API.md): never on a candidate-facing path.

### 4.3 The queued path degrades gracefully, and stays observable

During the async and batch bursts the probe stayed healthy — **p50 79–84 ms, p95 102–207 ms**,
zero failures, with one 2,466 ms outlier in run 1 coinciding with the 40-socket create burst.
Queue depth tracked the burst honestly: `0 → 32–38 → 0`. Poll latency never exceeded 240 ms
(Python) or 643 ms (Java). **Everything you need for backpressure is readable while the box is
saturated**, which is the property `wait=true` destroys.

### 4.4 Read path

40 simultaneous `GET /submissions/:token` on terminal submissions: **39.3 req/s, p50 339 ms,
max 993 ms, all 200**. Reads are cheap, but note p50 is still 339 ms under a 40-wide burst versus
~100 ms warm — the same 10-thread pool serves reads and writes, so a submit burst does slow
result polling. Another argument for batched polls (1 request per 20 tokens).

---

## 5. Capacity projections

Measured sustained rates: **2.75 sub/s Python, 0.75 sub/s Java** (planning numbers, taken as the
low end of the observed range).

| Real-world burst | Submissions | Python | Java |
|---|---:|---:|---:|
| 40 candidates click **Run sample** (2 tests) | 80 | 29 s | 107 s |
| 40 candidates click **Submit** (10 hidden tests) | 400 | **145 s** | **536 s (8.9 min)** |
| 40 candidates, 20 tests | 800 | 291 s | 1,067 s |
| Steady state: 40 candidates, submit every 5 min, 10 tests | 1.33 sub/s | 48 % utilised — OK | **178 % — over capacity** |
| Steady state: 40 candidates, submit every 3 min, 10 tests | 2.22 sub/s | 81 % — tight | over capacity |
| Steady state: 40 candidates, submit every 2 min, 10 tests | 3.33 sub/s | **121 % — backlog grows** | over capacity |

The **last candidate in a 400-submission burst waits ~2.5 minutes** for a verdict on Python and
~9 minutes on Java, even though every individual program runs in 0.02–0.2 s of CPU. That queue
wait, not correctness or errors, is the user-visible failure mode of this box.

Also worth noting: 40 simultaneous submissions only reached **7.6 % of `MAX_QUEUE_SIZE`**. You
would need ~500 in flight to see a 503 — by which time the backlog is already ~3 minutes deep.
**503 is not a usable capacity signal**; queue depth from `GET /workers` is.

---

## 6. Recommendations, ranked by measured impact

1. **Cap in-flight grading jobs at 2–4 in your own backend queue.** The measurements are flat from
   4 to 40 concurrent — throughput is pinned at ~2.8 sub/s by the 2 workers. Extra concurrency
   buys nothing and only inflates create-POST p99 (618 ms → 2,927 ms).
2. **Never use `wait=true` on a candidate path.** It is measurably faster in isolation (9.9 vs
   2.8 sub/s) and that is why someone will reach for it. It cost 1.35 s of control-plane latency
   with *trivial* programs and reported queue depth 0 the whole time.
3. **Use `POST /submissions/batch` and batched polling.** 2 requests instead of 40 to enqueue,
   0.5 s instead of 2.9 s, and p99 create latency 4.7× lower.
4. **Gate on `GET /workers[0].size`, not on 503.** Depth tracked the load faithfully (0 → 38 → 0)
   and the endpoint stayed responsive (p95 ≈ 100–200 ms) throughout every burst.
5. **Treat Java as a separate capacity class.** 0.75 sub/s measured — 3.7× worse than Python and
   about half what the API doc estimates. Either give JVM languages their own queue with a lower
   admission rate, or bundle hidden Java test cases (§15.2 of the API doc).
6. **Per-candidate cooldown of 20–30 s and one in-flight attempt per candidate.** This is what
   converts the 400-submission cliff into a bounded 1.33 sub/s stream that the box handles at 48 %
   utilisation.
7. **Show queue position, not a spinner.** At p50 8 s / p95 14 s for a 40-wide Python burst and
   28 s / 49 s for Java, candidates need to see that they are queued, not stuck.

---

## 7. Reproducing

```bash
cd loadtest
./run.sh -c 40                                   # all scenarios, Python 3.8.1
./run.sh -c 40 --scenarios async,batch,wait      # skip baseline/read
./run.sh -c 40 --language 62 --scenarios async   # Java
./run.sh -c 40 --no-probe --tag quiet            # without the control-plane probe
```

`run.sh` extracts `AUTHN_TOKEN` from `../../secret.txt` (outside the repo) into the process environment at run time;
the token is never written into the harness or into any output file. Set `JUDGE0_TOKEN` yourself
to override. Each run writes `results-<c>c-<timestamp>[-tag].json` (every per-request sample,
including the queue-depth time series) and a per-run `REPORT-<c>c-<timestamp>[-tag].md`.

Artifacts behind this report:

| Run | JSON | Per-run report |
|---|---|---|
| Python, full | [results-40c-20260807T163904Z.json](results-40c-20260807T163904Z.json) | [REPORT-40c-20260807T163904Z.md](REPORT-40c-20260807T163904Z.md) |
| Python, repeat | [results-40c-20260807T164016Z-rep2.json](results-40c-20260807T164016Z-rep2.json) | [REPORT-40c-20260807T164016Z-rep2.md](REPORT-40c-20260807T164016Z-rep2.md) |
| Java | [results-40c-20260807T164126Z-java.json](results-40c-20260807T164126Z-java.json) | [REPORT-40c-20260807T164126Z-java.md](REPORT-40c-20260807T164126Z-java.md) |

### Caveats

- Run from a residential/office network over plain HTTP to `65.0.29.135`. Absolute latencies
  include ~70–100 ms of internet RTT (visible as the probe's 45–72 ms floor); the *differences*
  between scenarios are the signal, not the absolute floors.
- Scenarios are separated by a 12 s settle and the queue was verified at depth 0 before each, so
  they do not contaminate each other.
- 3 baseline samples is a reference point, not a distribution.
- Every run adds 40–200 permanent rows to the Judge0 Postgres database — `enable_submission_delete`
  is `false`, so load-test submissions cannot be cleaned up through the API (§16 of the API doc).
  This session created **283 submissions** (123 + 120 + 40) plus 5 from a 4-concurrency smoke test.
- Not tested: sustained load over minutes, memory pressure (all programs ran well under
  `memory_limit`), TLE/compile-error paths under concurrency, or concurrency above 40.
