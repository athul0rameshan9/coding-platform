#!/usr/bin/env python3
"""
Judge0 load test harness.

Drives a configurable number of simultaneous requests against a Judge0
deployment and reports latency distributions, throughput, queue depth and
control-plane responsiveness under load.

The auth token is read from the environment (JUDGE0_TOKEN) and is never
written to disk by this script.

Scenarios
---------
baseline   serial wait=true submissions on an idle box (reference latency)
async      N simultaneous POST /submissions?wait=false, then batched polling
           until every token reaches a terminal status (status_id > 2).
           This is the production-shaped path.
batch      the same N submissions dispatched as ceil(N/20) batch POSTs.
wait       N simultaneous POST /submissions?wait=true. Exercises the Rails
           thread pool (RAILS_SERVER_PROCESSES x RAILS_MAX_THREADS = 10 on
           this box) rather than the judge workers.
read       N simultaneous GET /submissions/:token on already-terminal
           submissions. Pure read path, no worker involvement.

During async/batch/wait a background probe hits GET /workers once per
250 ms to measure control-plane latency and live queue depth while the
load is in flight.

Usage
-----
    export JUDGE0_URL=http://<IP>:2358 JUDGE0_TOKEN=...
    python3 judge0_loadtest.py --concurrency 40
    python3 judge0_loadtest.py -c 40 --scenarios async,batch,wait,read
    python3 judge0_loadtest.py -c 40 --language 62      # Java
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter

MAX_BATCH = 20  # config_info.max_submission_batch_size
TERMINAL = lambda sid: sid is not None and sid > 2  # noqa: E731  (§3 of JUDGE0_API.md)

POLL_FIELDS = "token,status_id,time,memory,exit_code,created_at,finished_at,message"

# Trivial, deterministic workloads. Keep the program cheap so the test measures
# the judge's dispatch capacity, not the candidate code.
PROGRAMS: dict[int, str] = {
    71: "a,b=map(int,input().split())\nprint(a+b)",                     # Python 3.8.1
    63: 'const [a,b]=require("fs").readFileSync(0,"utf8").trim()'
        ".split(/\\s+/).map(Number);console.log(a+b);",                  # Node 12
    54: "#include <iostream>\nint main(){long a,b;std::cin>>a>>b;"
        "std::cout<<a+b<<std::endl;}",                                   # C++ GCC 9.2.0
    62: "import java.util.Scanner;\npublic class Main{public static void "
        "main(String[] a){Scanner s=new Scanner(System.in);"
        "System.out.println(s.nextLong()+s.nextLong());}}",              # Java 13
}


def b64(s: str | None) -> str | None:
    return None if s is None else base64.b64encode(s.encode("utf-8")).decode("ascii")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# measurement primitives
# --------------------------------------------------------------------------- #

@dataclass
class Sample:
    """One HTTP request."""
    label: str
    t_start: float          # monotonic, relative to scenario start
    t_end: float
    http_status: int | None
    ok: bool
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ms(self) -> float:
        return (self.t_end - self.t_start) * 1000.0


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile. Explicit so the report is reproducible."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(p / 100.0 * len(s)) - 1))
    return s[k]


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "n": len(values),
        "min": min(values),
        "p50": pct(values, 50),
        "mean": statistics.fmean(values),
        "p90": pct(values, 90),
        "p95": pct(values, 95),
        "p99": pct(values, 99),
        "max": max(values),
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #

class Judge0Client:
    def __init__(self, base: str, token: str, pool: int, timeout: float):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({
            "X-Auth-Token": token,
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        })
        adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool, max_retries=0)
        self.s.mount("http://", adapter)
        self.s.mount("https://", adapter)

    def raw(self, method: str, path: str, **kw) -> requests.Response:
        return self.s.request(method, f"{self.base}{path}", timeout=self.timeout, **kw)

    def timed(self, label: str, origin: float, method: str, path: str, **kw) -> tuple[Sample, Any]:
        """One measured request. Never raises; failures land in the Sample."""
        t0 = time.monotonic()
        try:
            r = self.raw(method, path, **kw)
            t1 = time.monotonic()
            try:
                body = r.json()
            except ValueError:
                body = r.text
            return Sample(label, t0 - origin, t1 - origin, r.status_code,
                          200 <= r.status_code < 300), body
        except Exception as e:  # timeouts, connection resets, refused connections
            t1 = time.monotonic()
            return Sample(label, t0 - origin, t1 - origin, None, False,
                          error=f"{type(e).__name__}: {e}"), None

    # -- convenience ------------------------------------------------------- #

    def queue_size(self) -> int | None:
        try:
            r = self.raw("GET", "/workers")
            if r.ok:
                return r.json()[0].get("size")
        except Exception:
            pass
        return None

    def poll_batch(self, tokens: list[str]) -> list[dict | None]:
        out: list[dict | None] = []
        for i in range(0, len(tokens), MAX_BATCH):
            chunk = tokens[i:i + MAX_BATCH]
            r = self.raw(
                "GET", "/submissions/batch",
                params={"tokens": ",".join(chunk), "base64_encoded": "true",
                        "fields": POLL_FIELDS},
            )
            r.raise_for_status()
            out.extend(r.json().get("submissions", [None] * len(chunk)))
        return out


def payload(language_id: int, i: int, cfg: argparse.Namespace) -> dict[str, Any]:
    a, b = i, i * 7 + 1
    return {
        "language_id": language_id,
        "source_code": b64(PROGRAMS[language_id]),
        "stdin": b64(f"{a} {b}"),
        "expected_output": b64(str(a + b)),
        # Send limits explicitly — never inherit deployment defaults (§15.4).
        "cpu_time_limit": cfg.cpu_time_limit,
        "wall_time_limit": cfg.wall_time_limit,
        "memory_limit": cfg.memory_limit,
        "number_of_runs": 1,
        "redirect_stderr_to_stdout": False,
        "enable_network": False,
    }


# --------------------------------------------------------------------------- #
# background probe: is the API still answering while we hammer it?
# --------------------------------------------------------------------------- #

class Probe(threading.Thread):
    """Samples GET /workers on its own connection during a scenario."""

    def __init__(self, base: str, token: str, origin: float, interval: float = 0.25):
        super().__init__(daemon=True)
        self.client = Judge0Client(base, token, pool=2, timeout=20.0)
        self.origin = origin
        self.interval = interval
        self.stop_flag = threading.Event()
        self.samples: list[Sample] = []
        self.queue_depth: list[tuple[float, int | None]] = []

    def run(self) -> None:
        while not self.stop_flag.is_set():
            s, body = self.client.timed("probe:/workers", self.origin, "GET", "/workers")
            self.samples.append(s)
            size = None
            if isinstance(body, list) and body:
                size = body[0].get("size")
            self.queue_depth.append((round(s.t_end, 3), size))
            self.stop_flag.wait(self.interval)

    def stop(self) -> dict[str, Any]:
        self.stop_flag.set()
        self.join(timeout=30)
        lat = [s.ms for s in self.samples if s.ok]
        return {
            "requests": len(self.samples),
            "failed": sum(1 for s in self.samples if not s.ok),
            "latency_ms": summarize(lat),
            "queue_depth_max": max((d for _, d in self.queue_depth if d is not None),
                                   default=None),
            "queue_depth_series": self.queue_depth,
        }


# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #

def fan_out(n: int, fn: Callable[[int], Any], workers: int) -> list[Any]:
    """Release n tasks as simultaneously as a thread pool allows.

    All threads are pre-spawned and wait on a barrier, so the requests leave
    the client together instead of ramping up as the pool warms.
    """
    barrier = threading.Barrier(n)
    results: list[Any] = [None] * n

    def wrapped(i: int) -> None:
        barrier.wait()
        results[i] = fn(i)

    with ThreadPoolExecutor(max_workers=max(workers, n)) as ex:
        list(ex.map(wrapped, range(n)))
    return results


def drain(client: Judge0Client, tokens: list[str], origin: float,
          cfg: argparse.Namespace) -> tuple[dict[str, dict], list[Sample], int]:
    """Poll until every token is terminal. Returns per-token results, poll
    samples, and the number of poll cycles."""
    pending = set(tokens)
    results: dict[str, dict] = {}
    polls: list[Sample] = []
    cycles = 0
    delay = cfg.poll_interval
    deadline = time.monotonic() + cfg.deadline

    while pending and time.monotonic() < deadline:
        time.sleep(delay)
        cycles += 1
        order = [t for t in tokens if t in pending]
        for i in range(0, len(order), MAX_BATCH):
            chunk = order[i:i + MAX_BATCH]
            s, body = client.timed(
                "poll:/submissions/batch", origin, "GET", "/submissions/batch",
                params={"tokens": ",".join(chunk), "base64_encoded": "true",
                        "fields": POLL_FIELDS})
            polls.append(s)
            if not s.ok or not isinstance(body, dict):
                continue
            for slot in body.get("submissions", []):
                if not slot:
                    continue  # unknown token -> null slot (§7)
                tok, sid = slot.get("token"), slot.get("status_id")
                if TERMINAL(sid) and tok in pending:
                    pending.discard(tok)
                    slot["_observed_at"] = round(s.t_end, 4)
                    results[tok] = slot
        # gentle backoff, capped, so long jobs don't spin the API
        delay = min(delay * 1.25, cfg.poll_max_interval)

    for tok in pending:
        results[tok] = {"token": tok, "status_id": None, "_timeout": True}
    return results, polls, cycles


def scenario_baseline(client: Judge0Client, cfg: argparse.Namespace) -> dict:
    """Serial wait=true round trips — reference latency on the current box."""
    origin = time.monotonic()
    samples: list[Sample] = []
    for i in range(cfg.baseline_runs):
        s, body = client.timed(
            "baseline:wait=true", origin, "POST", "/submissions",
            params={"base64_encoded": "true", "wait": "true"},
            json=payload(cfg.language, 1000 + i, cfg))
        if isinstance(body, dict):
            s.extra["status_id"] = (body.get("status") or {}).get("id")
        samples.append(s)
    lat = [s.ms for s in samples if s.ok]
    return {
        "kind": "baseline",
        "description": f"{cfg.baseline_runs} serial wait=true submissions",
        "requests": len(samples),
        "failed": sum(1 for s in samples if not s.ok),
        "latency_ms": summarize(lat),
        "samples": [asdict(s) for s in samples],
    }


def _submission_scenario(client: Judge0Client, cfg: argparse.Namespace,
                         mode: str) -> dict:
    """async (N individual POSTs) or batch (ceil(N/20) batch POSTs)."""
    n = cfg.concurrency
    origin = time.monotonic()
    probe = Probe(cfg.base, cfg.token, origin) if cfg.probe else None
    if probe:
        probe.start()
    q_before = client.queue_size()

    create: list[Sample] = []
    tokens: list[str] = []
    errors: list[dict] = []

    t_submit0 = time.monotonic()
    if mode == "async":
        def one(i: int):
            return client.timed(
                f"create:{i}", origin, "POST", "/submissions",
                params={"base64_encoded": "true", "wait": "false"},
                json=payload(cfg.language, i, cfg))

        for s, body in fan_out(n, one, cfg.concurrency):
            create.append(s)
            if isinstance(body, dict) and body.get("token"):
                tokens.append(body["token"])
            else:
                errors.append({"http": s.http_status, "body": body, "error": s.error})
    else:
        groups = [list(range(i, min(i + MAX_BATCH, n))) for i in range(0, n, MAX_BATCH)]

        def one_batch(gi: int):
            return client.timed(
                f"create-batch:{gi}", origin, "POST", "/submissions/batch",
                params={"base64_encoded": "true"},
                json={"submissions": [payload(cfg.language, i, cfg)
                                      for i in groups[gi]]})

        for s, body in fan_out(len(groups), one_batch, len(groups)):
            create.append(s)
            if isinstance(body, list):
                for slot in body:
                    if isinstance(slot, dict) and slot.get("token"):
                        tokens.append(slot["token"])
                    else:
                        errors.append({"http": s.http_status, "slot": slot})
            else:
                errors.append({"http": s.http_status, "body": body, "error": s.error})
    t_submit1 = time.monotonic()

    q_after_submit = client.queue_size()
    results, polls, cycles = drain(client, tokens, origin, cfg)
    t_done = time.monotonic()
    probe_stats = probe.stop() if probe else None

    statuses: dict[str, int] = {}
    for r in results.values():
        statuses[str(r.get("status_id"))] = statuses.get(str(r.get("status_id")), 0) + 1

    # server-side truth: finished_at - created_at, plus queue wait
    server_ms, e2e_ms, cpu_s = [], [], []
    for r in results.values():
        ca, fa = r.get("created_at"), r.get("finished_at")
        if ca and fa:
            try:
                server_ms.append(
                    (datetime.fromisoformat(fa.replace("Z", "+00:00"))
                     - datetime.fromisoformat(ca.replace("Z", "+00:00"))).total_seconds() * 1000)
            except ValueError:
                pass
        if r.get("_observed_at") is not None:
            e2e_ms.append(r["_observed_at"] * 1000)
        if r.get("time") is not None:
            try:
                cpu_s.append(float(r["time"]))
            except (TypeError, ValueError):
                pass

    accepted = statuses.get("3", 0)
    wall = t_done - origin
    return {
        "kind": mode,
        "description": (f"{n} simultaneous POST /submissions (wait=false)" if mode == "async"
                        else f"{n} submissions via {math.ceil(n / MAX_BATCH)} batch POSTs"),
        "concurrency": n,
        "language_id": cfg.language,
        "create_requests": len(create),
        "create_failed": sum(1 for s in create if not s.ok),
        "tokens_returned": len(tokens),
        "create_latency_ms": summarize([s.ms for s in create if s.ok]),
        "submit_window_s": round(t_submit1 - t_submit0, 4),
        "submit_rate_per_s": round(len(tokens) / max(t_submit1 - t_submit0, 1e-9), 2),
        "poll_requests": len(polls),
        "poll_cycles": cycles,
        "poll_latency_ms": summarize([s.ms for s in polls if s.ok]),
        "poll_failed": sum(1 for s in polls if not s.ok),
        "wall_s": round(wall, 3),
        "throughput_sub_per_s": round(len(results) / max(wall, 1e-9), 3),
        "time_to_terminal_ms": summarize(e2e_ms),
        "server_exec_ms": summarize(server_ms),
        "cpu_time_s": summarize(cpu_s),
        "statuses": statuses,
        "accepted": accepted,
        "correct_pct": round(100.0 * accepted / max(len(results), 1), 1),
        "timed_out": sum(1 for r in results.values() if r.get("_timeout")),
        "queue_size_before": q_before,
        "queue_size_after_submit": q_after_submit,
        "errors": errors[:20],
        "probe": probe_stats,
        "samples": [asdict(s) for s in create],
        "_tokens": tokens,
        "_results": results,
    }


def scenario_wait(client: Judge0Client, cfg: argparse.Namespace) -> dict:
    """N simultaneous wait=true calls — hits the 10-thread Rails pool (§5)."""
    n = cfg.concurrency
    origin = time.monotonic()
    probe = Probe(cfg.base, cfg.token, origin) if cfg.probe else None
    if probe:
        probe.start()

    def one(i: int):
        return client.timed(
            f"wait:{i}", origin, "POST", "/submissions",
            params={"base64_encoded": "true", "wait": "true"},
            json=payload(cfg.language, 2000 + i, cfg))

    t0 = time.monotonic()
    out = fan_out(n, one, n)
    t1 = time.monotonic()
    probe_stats = probe.stop() if probe else None

    samples: list[Sample] = []
    statuses: dict[str, int] = {}
    for s, body in out:
        if isinstance(body, dict):
            sid = (body.get("status") or {}).get("id")
            s.extra["status_id"] = sid
            statuses[str(sid)] = statuses.get(str(sid), 0) + 1
        samples.append(s)

    lat = [s.ms for s in samples if s.ok]
    return {
        "kind": "wait",
        "description": f"{n} simultaneous POST /submissions?wait=true",
        "concurrency": n,
        "requests": len(samples),
        "failed": sum(1 for s in samples if not s.ok),
        "http_codes": _codes(samples),
        "latency_ms": summarize(lat),
        "wall_s": round(t1 - t0, 3),
        "throughput_sub_per_s": round(n / max(t1 - t0, 1e-9), 3),
        "statuses": statuses,
        "accepted": statuses.get("3", 0),
        "probe": probe_stats,
        "samples": [asdict(s) for s in samples],
    }


def scenario_read(client: Judge0Client, cfg: argparse.Namespace,
                  tokens: list[str]) -> dict:
    """N simultaneous GETs of already-terminal submissions (pure read path)."""
    n = cfg.concurrency
    if not tokens:
        return {"kind": "read", "skipped": "no tokens available from earlier scenarios"}
    origin = time.monotonic()

    def one(i: int):
        tok = tokens[i % len(tokens)]
        return client.timed(
            f"read:{i}", origin, "GET", f"/submissions/{tok}",
            params={"base64_encoded": "true", "fields": POLL_FIELDS})

    t0 = time.monotonic()
    out = fan_out(n, one, n)
    t1 = time.monotonic()
    samples = [s for s, _ in out]
    return {
        "kind": "read",
        "description": f"{n} simultaneous GET /submissions/:token (terminal, cached ~1s)",
        "concurrency": n,
        "requests": len(samples),
        "failed": sum(1 for s in samples if not s.ok),
        "http_codes": _codes(samples),
        "latency_ms": summarize([s.ms for s in samples if s.ok]),
        "wall_s": round(t1 - t0, 3),
        "throughput_req_per_s": round(n / max(t1 - t0, 1e-9), 2),
        "samples": [asdict(s) for s in samples],
    }


def _codes(samples: list[Sample]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in samples:
        k = str(s.http_status) if s.http_status else (s.error or "error").split(":")[0]
        out[k] = out.get(k, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def fmt(v: Any, nd: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def lat_row(name: str, d: dict) -> str:
    if not d:
        return f"| {name} | — | — | — | — | — | — | — |"
    return (f"| {name} | {d['n']} | {fmt(d['min'])} | {fmt(d['p50'])} | {fmt(d['p90'])} "
            f"| {fmt(d['p95'])} | {fmt(d['p99'])} | {fmt(d['max'])} |")


def write_report(run: dict, path: str) -> None:
    cfg = run["config"]
    L: list[str] = []
    A = L.append
    A(f"# Judge0 load test — {cfg['concurrency']} simultaneous requests")
    A("")
    A(f"- **Target**: `{cfg['base']}` (Judge0 {run.get('about', {}).get('version', '?')})")
    A(f"- **Run at**: {run['started_at']}  ·  duration {fmt(run['duration_s'], 1)} s")
    A(f"- **Concurrency**: {cfg['concurrency']} simultaneous requests per scenario "
      f"(released together via a thread barrier)")
    A(f"- **Workload**: language_id {cfg['language']}, trivial `a+b`, "
      f"`cpu_time_limit={cfg['cpu_time_limit']}` `wall_time_limit={cfg['wall_time_limit']}` "
      f"`memory_limit={cfg['memory_limit']}`, `base64_encoded=true`")
    A(f"- **Client**: {os.uname().nodename}, python {sys.version.split()[0]}, "
      f"keep-alive pool of {cfg['concurrency']}")
    A("")

    cap = run.get("config_info", {})
    if cap:
        A("Server config at test time: "
          f"`max_queue_size={cap.get('max_queue_size')}`, "
          f"`max_submission_batch_size={cap.get('max_submission_batch_size')}`, "
          f"`enable_wait_result={cap.get('enable_wait_result')}`, "
          f"`submission_cache_duration={cap.get('submission_cache_duration')}`.")
        A("")

    A("## Headline numbers")
    A("")
    A("| Scenario | Requests | Wall clock | Throughput | Errors | Correct verdicts |")
    A("|---|---:|---:|---:|---:|---:|")
    for sc in run["scenarios"]:
        k = sc["kind"]
        if sc.get("skipped"):
            A(f"| {k} | — | — | — | — | skipped: {sc['skipped']} |")
            continue
        if k in ("async", "batch"):
            reqs = f"{sc['create_requests']} create + {sc['poll_requests']} poll"
            thr = f"{fmt(sc['throughput_sub_per_s'], 2)} sub/s"
            err = sc["create_failed"] + sc["poll_failed"] + sc["timed_out"]
            corr = f"{sc['accepted']}/{sc['tokens_returned']} ({fmt(sc['correct_pct'])}%)"
            A(f"| **{k}** | {reqs} | {fmt(sc['wall_s'], 2)} s | {thr} | {err} | {corr} |")
        elif k == "wait":
            A(f"| **wait** | {sc['requests']} | {fmt(sc['wall_s'], 2)} s | "
              f"{fmt(sc['throughput_sub_per_s'], 2)} sub/s | {sc['failed']} | "
              f"{sc['accepted']}/{sc['requests']} |")
        elif k == "read":
            A(f"| **read** | {sc['requests']} | {fmt(sc['wall_s'], 2)} s | "
              f"{fmt(sc['throughput_req_per_s'], 1)} req/s | {sc['failed']} | n/a |")
        elif k == "baseline":
            A(f"| baseline (serial) | {sc['requests']} | — | — | {sc['failed']} | n/a |")
    A("")

    A("## Latency distributions (ms)")
    A("")
    A("| Measurement | n | min | p50 | p90 | p95 | p99 | max |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for sc in run["scenarios"]:
        k = sc["kind"]
        if sc.get("skipped"):
            continue
        if k == "baseline":
            A(lat_row("baseline serial `wait=true`", sc["latency_ms"]))
        elif k in ("async", "batch"):
            A(lat_row(f"{k}: create (POST)", sc["create_latency_ms"]))
            A(lat_row(f"{k}: batch poll (GET)", sc["poll_latency_ms"]))
            A(lat_row(f"{k}: time to terminal (from t0)", sc["time_to_terminal_ms"]))
            A(lat_row(f"{k}: server exec (finished_at−created_at)", sc["server_exec_ms"]))
        elif k == "wait":
            A(lat_row("wait=true under load", sc["latency_ms"]))
        elif k == "read":
            A(lat_row("read GET /submissions/:token", sc["latency_ms"]))
    A("")

    for sc in run["scenarios"]:
        if sc.get("skipped") or sc["kind"] == "baseline":
            continue
        A(f"## Scenario `{sc['kind']}` — {sc['description']}")
        A("")
        if sc["kind"] in ("async", "batch"):
            A(f"- Enqueued **{sc['tokens_returned']}/{sc['concurrency']}** submissions in "
              f"**{fmt(sc['submit_window_s'], 3)} s** ({fmt(sc['submit_rate_per_s'], 1)} POST/s)")
            A(f"- Drained all tokens in **{fmt(sc['wall_s'], 2)} s** over "
              f"{sc['poll_cycles']} poll cycles / {sc['poll_requests']} poll requests")
            A(f"- Effective throughput: **{fmt(sc['throughput_sub_per_s'], 2)} submissions/sec**")
            A(f"- Queue depth (`GET /workers[0].size`): {sc['queue_size_before']} before → "
              f"{sc['queue_size_after_submit']} right after submit → "
              f"peak {fmt((sc.get('probe') or {}).get('queue_depth_max'), 0)} during the run")
            A(f"- Statuses: `{json.dumps(sc['statuses'])}` "
              f"({sc['accepted']} Accepted, {sc['timed_out']} never reached terminal)")
            if sc["cpu_time_s"]:
                A(f"- Reported CPU time per submission: p50 "
                  f"{fmt(sc['cpu_time_s']['p50'], 3)} s, max {fmt(sc['cpu_time_s']['max'], 3)} s")
        elif sc["kind"] == "wait":
            A(f"- HTTP codes: `{json.dumps(sc['http_codes'])}`")
            A(f"- All {sc['requests']} calls completed in **{fmt(sc['wall_s'], 2)} s**; "
              f"latency p50 {fmt(sc['latency_ms'].get('p50'))} ms → "
              f"p99 {fmt(sc['latency_ms'].get('p99'))} ms")
            A(f"- Spread (max/min) = "
              f"{fmt(sc['latency_ms']['max'] / max(sc['latency_ms']['min'], 1e-9), 1)}× — "
              f"head-of-line queueing in the Rails thread pool")
        elif sc["kind"] == "read":
            A(f"- HTTP codes: `{json.dumps(sc['http_codes'])}`")
        if sc.get("errors"):
            A(f"- First errors: `{json.dumps(sc['errors'][:3])}`")
        p = sc.get("probe")
        if p:
            A("")
            A(f"**Control-plane probe during this scenario** (`GET /workers` every 250 ms): "
              f"{p['requests']} probes, {p['failed']} failed, latency p50 "
              f"{fmt(p['latency_ms'].get('p50'))} ms / p95 {fmt(p['latency_ms'].get('p95'))} ms "
              f"/ max {fmt(p['latency_ms'].get('max'))} ms. Peak queue depth "
              f"{fmt(p['queue_depth_max'], 0)}.")
            series = ", ".join(f"{t}s:{d}" for t, d in p["queue_depth_series"][:40])
            A("")
            A(f"Queue-depth series (t:size): {series}")
        A("")

    A("## Raw data")
    A("")
    A(f"Full per-request samples: `{os.path.basename(run['_json_path'])}`")
    A("")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="Judge0 load test")
    ap.add_argument("--base", default=os.environ.get("JUDGE0_URL"), required=not os.environ.get("JUDGE0_URL"))
    ap.add_argument("-c", "--concurrency", type=int, default=40)
    ap.add_argument("--scenarios", default="baseline,async,batch,wait,read")
    ap.add_argument("--language", type=int, default=71, choices=sorted(PROGRAMS))
    ap.add_argument("--cpu-time-limit", type=float, default=2.0)
    ap.add_argument("--wall-time-limit", type=float, default=5.0)
    ap.add_argument("--memory-limit", type=int, default=128000)
    ap.add_argument("--poll-interval", type=float, default=0.5)
    ap.add_argument("--poll-max-interval", type=float, default=2.0)
    ap.add_argument("--deadline", type=float, default=180.0,
                    help="seconds to wait for all submissions to reach a terminal status")
    ap.add_argument("--timeout", type=float, default=120.0, help="per-request HTTP timeout")
    ap.add_argument("--baseline-runs", type=int, default=3)
    ap.add_argument("--settle", type=float, default=10.0,
                    help="idle seconds between scenarios so the queue drains")
    ap.add_argument("--no-probe", dest="probe", action="store_false", default=True)
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--tag", default="")
    cfg = ap.parse_args()

    token = os.environ.get("JUDGE0_TOKEN")
    if not token:
        print("JUDGE0_TOKEN is not set", file=sys.stderr)
        return 2
    cfg.token = token

    client = Judge0Client(cfg.base, token, pool=max(cfg.concurrency * 2, 32), timeout=cfg.timeout)

    about = config_info = {}
    try:
        about = client.raw("GET", "/about").json()
        config_info = client.raw("GET", "/config_info").json()
    except Exception as e:
        print(f"warning: preflight failed: {e}", file=sys.stderr)

    wanted = [s.strip() for s in cfg.scenarios.split(",") if s.strip()]
    started = now_iso()
    t0 = time.monotonic()
    scenarios: list[dict] = []
    terminal_tokens: list[str] = []

    for i, name in enumerate(wanted):
        if i and cfg.settle:
            print(f"  … settling {cfg.settle}s (queue depth "
                  f"{client.queue_size()})", flush=True)
            time.sleep(cfg.settle)
        print(f"[{i + 1}/{len(wanted)}] scenario: {name}", flush=True)
        if name == "baseline":
            sc = scenario_baseline(client, cfg)
        elif name in ("async", "batch"):
            sc = _submission_scenario(client, cfg, name)
        elif name == "wait":
            sc = scenario_wait(client, cfg)
        elif name == "read":
            sc = scenario_read(client, cfg, terminal_tokens)
        else:
            print(f"  unknown scenario {name!r}, skipping", file=sys.stderr)
            continue
        scenarios.append(sc)
        if name in ("async", "batch"):
            # keep a few known-terminal tokens around for the read scenario
            terminal_tokens += sc.get("_tokens", [])[:20]
        _print_brief(sc)

    duration = time.monotonic() - t0
    suffix = f"-{cfg.tag}" if cfg.tag else ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jpath = os.path.join(cfg.outdir, f"results-{cfg.concurrency}c-{stamp}{suffix}.json")
    mpath = os.path.join(cfg.outdir, f"REPORT-{cfg.concurrency}c-{stamp}{suffix}.md")

    public_cfg = {k: v for k, v in vars(cfg).items() if k != "token"}
    run = {
        "started_at": started,
        "duration_s": duration,
        "config": public_cfg,
        "about": about,
        "config_info": config_info,
        "scenarios": scenarios,
        "_json_path": jpath,
    }
    with open(jpath, "w") as f:
        json.dump(run, f, indent=2, default=str)
    write_report(run, mpath)
    print(f"\nJSON:   {jpath}\nReport: {mpath}")
    return 0


def _print_brief(sc: dict) -> None:
    k = sc["kind"]
    if sc.get("skipped"):
        print(f"  skipped: {sc['skipped']}")
        return
    if k == "baseline":
        d = sc["latency_ms"]
        print(f"  serial wait=true: p50 {d.get('p50', 0):.0f} ms, max {d.get('max', 0):.0f} ms")
    elif k in ("async", "batch"):
        print(f"  enqueued {sc['tokens_returned']} in {sc['submit_window_s']:.2f}s "
              f"({sc['submit_rate_per_s']:.1f} POST/s); all terminal in {sc['wall_s']:.2f}s "
              f"({sc['throughput_sub_per_s']:.2f} sub/s); statuses {sc['statuses']}")
        p = sc.get("probe") or {}
        if p:
            print(f"  probe: p95 {p['latency_ms'].get('p95', 0):.0f} ms, "
                  f"max {p['latency_ms'].get('max', 0):.0f} ms, "
                  f"peak queue {p.get('queue_depth_max')}")
    elif k == "wait":
        d = sc["latency_ms"]
        print(f"  wait=true x{sc['concurrency']}: wall {sc['wall_s']:.2f}s, "
              f"p50 {d.get('p50', 0):.0f} ms, p99 {d.get('p99', 0):.0f} ms, "
              f"max {d.get('max', 0):.0f} ms, failed {sc['failed']}")
        p = sc.get("probe") or {}
        if p:
            print(f"  probe: p95 {p['latency_ms'].get('p95', 0):.0f} ms, "
                  f"max {p['latency_ms'].get('max', 0):.0f} ms, failed {p['failed']}")
    elif k == "read":
        d = sc["latency_ms"]
        print(f"  read x{sc['concurrency']}: wall {sc['wall_s']:.2f}s, "
              f"p50 {d.get('p50', 0):.0f} ms, max {d.get('max', 0):.0f} ms, "
              f"codes {sc['http_codes']}")


if __name__ == "__main__":
    sys.exit(main())
