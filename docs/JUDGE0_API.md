# Judge0 v1.13.1 — API Reference for the Coding-Test Platform

Operational reference for the live Judge0 deployment at `http://65.0.29.135:2358`.
Every value, table, and JSON transcript in this document was captured from that server, not from
upstream documentation. Where a behaviour could only be confirmed from the Judge0 v1.13.1 source
(callback wire format), it is labelled as such.

| | |
|---|---|
| Base URL | `http://65.0.29.135:2358` (plain HTTP — no TLS) |
| Judge0 version | `1.13.1` (from `GET /about`) |
| Auth header | `X-Auth-Token` |
| Host | Intel Xeon Platinum 8259CL, 2 vCPU, 913 MiB RAM + 2 GiB swap, Ubuntu 22.04 |
| Workers | `COUNT=2` |
| Rails | `RAILS_SERVER_PROCESSES=2`, `RAILS_MAX_THREADS=5` (10 request threads total) |
| Languages | 47 active |

---

## Table of contents

1. [Quick start](#1-quick-start)
2. [Authentication and endpoint reachability](#2-authentication-and-endpoint-reachability)
3. [Submission lifecycle](#3-submission-lifecycle)
4. [`POST /submissions`](#4-post-submissions)
5. [`wait=true` vs `wait=false`](#5-waittrue-vs-waitfalse)
6. [`GET /submissions/:token`](#6-get-submissionstoken)
7. [Batch endpoints](#7-batch-endpoints)
8. [`base64_encoded=true`](#8-base64_encodedtrue)
9. [Status codes and result interpretation](#9-status-codes-and-result-interpretation)
10. [Output comparison semantics](#10-output-comparison-semantics)
11. [Languages (all 47)](#11-languages-all-47)
12. [Callbacks](#12-callbacks)
13. [Error responses](#13-error-responses)
14. [Enforced limits (`GET /config_info`)](#14-enforced-limits-get-config_info)
15. [Building a HackerRank-style platform](#15-building-a-hackerrank-style-platform)
16. [Capacity and rate limits for this box](#16-capacity-and-rate-limits-for-this-box)
17. [Ready-to-use client code](#17-ready-to-use-client-code)
18. [Admin / introspection endpoints](#18-admin--introspection-endpoints)
19. [Security notes](#19-security-notes)

---

## 1. Quick start

The token is **never** written into code or committed. Export it first:

```bash
export JUDGE0_URL="http://65.0.29.135:2358"
export JUDGE0_TOKEN="$(...)"   # value lives in secret.txt — see §19
```

Smallest end-to-end submission (synchronous, for manual poking only — see §5):

```bash
curl -s -X POST "$JUDGE0_URL/submissions?base64_encoded=false&wait=true" \
  -H "X-Auth-Token: $JUDGE0_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "language_id": 71,
        "source_code": "print(sum(map(int, input().split())))",
        "stdin": "3 4",
        "expected_output": "7"
      }'
```

Real response from the live server:

```json
{
  "stdout": "7\n",
  "time": "0.016",
  "memory": 3256,
  "stderr": null,
  "token": "89f0443c-b6c9-44e4-bce1-442959dfc14c",
  "compile_output": null,
  "message": null,
  "status": { "id": 3, "description": "Accepted" }
}
```

The production shape — async create, then poll:

```bash
# 1. create
curl -s -X POST "$JUDGE0_URL/submissions?base64_encoded=false&wait=false" \
  -H "X-Auth-Token: $JUDGE0_TOKEN" -H "Content-Type: application/json" \
  -d '{"language_id":63,"source_code":"const l=require(\"fs\").readFileSync(0,\"utf8\").trim();console.log(l.toUpperCase());","stdin":"hello judge0"}'
# -> {"token":"da49119b-da4f-4577-8a11-97a196dc9263"}

# 2. poll
curl -s -H "X-Auth-Token: $JUDGE0_TOKEN" \
  "$JUDGE0_URL/submissions/da49119b-da4f-4577-8a11-97a196dc9263?base64_encoded=false"
```

Immediately after creation (real response):

```json
{
  "stdout": null, "time": null, "memory": null, "stderr": null,
  "token": "da49119b-da4f-4577-8a11-97a196dc9263",
  "compile_output": null, "message": null,
  "status": { "id": 1, "description": "In Queue" }
}
```

~700 ms later (real response):

```json
{
  "stdout": "HELLO JUDGE0\n",
  "time": "0.058",
  "memory": 34220,
  "stderr": null,
  "token": "da49119b-da4f-4577-8a11-97a196dc9263",
  "compile_output": null,
  "message": null,
  "status": { "id": 3, "description": "Accepted" }
}
```

---

## 2. Authentication and endpoint reachability

Send `X-Auth-Token: <token>` on **every** request. There are no public endpoints on this
deployment — even `/about`, `/languages`, and `/statuses` return 401 without the header.

Verified HTTP codes with no token / a wrong token:

| Endpoint | No token | Wrong token | Valid submission token |
|---|---|---|---|
| `GET /about` | 401 | 401 | 200 |
| `GET /languages` | 401 | 401 | 200 |
| `GET /statuses` | 401 | 401 | 200 |
| `GET /config_info` | 401 | 401 | 200 |
| `GET /system_info` | 401 | 401 | 200 |
| `GET /statistics` | 401 | 401 | 200 |
| `GET /workers` | 401 | 401 | 200 |
| `POST /submissions` | 401 | 401 | 201 |
| `GET /submissions/:token` | 401 | 401 | 200 |
| `GET /submissions` (list **all**) | 401 | 401 | **403** |
| `DELETE /submissions/:token` | 401 | 401 | **403** |
| `POST /authorize` | 401 | 401 | **403** |

A 401 has an empty body and `Content-Type: text/html`:

```
HTTP/1.1 401 Unauthorized
Content-Type: text/html
Cache-Control: no-cache
X-Request-Id: 88ae33f2-4991-4faa-821a-3808c74a04de
X-Runtime: 0.001521
```

Do not parse 401 bodies — branch on the status code.

**Correction to the deployment brief:** the informational admin endpoints (`/workers`,
`/statistics`, `/config_info`, `/system_info`) *are* reachable with the plain submission token, as
stated. But two authorization-gated routes are **not**: `GET /submissions` (list every submission
in the database) and `POST /authorize` both return **403 Forbidden** with this token, and
`DELETE /submissions/:token` returns **403** because `enable_submission_delete` is `false`. You
cannot enumerate or delete submissions through this API — track your own tokens.

---

## 3. Submission lifecycle

```
POST /submissions  ──►  201 Created  {"token": "<uuid>"}
                              │
                              ▼
                     status.id = 1  "In Queue"      (Resque queue "1.13.1")
                              │  worker picks it up (COUNT=2 workers)
                              ▼
                     status.id = 2  "Processing"    (compile, then run in isolate)
                              │
                              ▼
        ┌──────── terminal ────────┐
        │ 3  Accepted              │  finished_at is set
        │ 4  Wrong Answer          │  results are immutable from here on
        │ 5  Time Limit Exceeded   │
        │ 6  Compilation Error     │
        │ 7–14 error variants      │
        └──────────────────────────┘
```

- The **token is a UUID v4** generated by Judge0 and is the only handle you ever get. There is no
  numeric id exposed, and there is no way to list submissions (403, §2). **Persist the token in
  your database inside the same transaction that records the grading attempt.** Losing a token
  means losing the result permanently.
- Tokens are permanent — `submission_cache_duration` (1.0 s) caches the *HTTP response*, not the
  record. Results remain fetchable indefinitely (`enable_submission_delete=false`, nothing purges).
- A status is **terminal iff `status.id > 2`**. That single predicate is the correct polling
  stop-condition; never test for `id == 3`.
- `status.id = 2` ("Processing") is real but short-lived and is easy to miss between polls. Do not
  build logic that requires observing it.
- `finished_at` is `null` until terminal; `created_at` is set at enqueue time.

---

## 4. `POST /submissions`

```
POST /submissions?base64_encoded={true|false}&wait={true|false}&fields=<csv>
Content-Type: application/json
X-Auth-Token: <token>
```

Returns **201 Created**. Body is `{"token": "<uuid>"}` when `wait=false`, or the full submission
object when `wait=true`.

Query parameters:

| Param | Default | Notes |
|---|---|---|
| `base64_encoded` | `false` | If `true`, `source_code`, `stdin`, `expected_output`, `additional_files` in the request must be base64; all text fields in the response come back base64. |
| `wait` | `false` | See §5. Allowed here because `enable_wait_result=true`. |
| `fields` | default set | Same semantics as on GET (§6); trims the `wait=true` response. |

### Request body fields

`can override?` = whether this deployment lets a client set the field at all (some are gated by
`allow_*` flags in `config_info`). `max` = the hard ceiling; exceeding it is a 422.

| Field | Type | Default on this box | Max / constraint | Can override? |
|---|---|---|---|---|
| `source_code` | string | — | required unless `language_id=89` | yes |
| `language_id` | integer | — | **required**; must exist (see §11) | yes |
| `stdin` | string | `null` | — | yes |
| `expected_output` | string | `null` | if omitted, any successful run is `Accepted` (§10) | yes |
| `cpu_time_limit` | float (s) | `5.0` | `≤ 15.0` | yes |
| `cpu_extra_time` | float (s) | `1.0` | `≤ 5.0` | yes |
| `wall_time_limit` | float (s) | `10.0` | `≤ 20.0` | yes |
| `memory_limit` | integer (KB) | `128000` | `≤ 512000` | yes |
| `stack_limit` | integer (KB) | `64000` | `≤ 128000` | yes |
| `max_processes_and_or_threads` | integer | `60` | `≤ 120` | yes |
| `enable_per_process_and_thread_time_limit` | boolean | `false` | — | yes (`allow_*=true`) |
| `enable_per_process_and_thread_memory_limit` | boolean | `false` | — | yes (`allow_*=true`) |
| `max_file_size` | integer (KB) | `1024` | `≤ 4096` | yes |
| `number_of_runs` | integer | `1` | `≤ 20` | yes |
| `redirect_stderr_to_stdout` | boolean | `false` | — | yes |
| `enable_network` | boolean | `false` | — | **yes** (`allow_enable_network=true`) — see §15 |
| `callback_url` | string (URL) | `null` | **not validated** — any string is accepted | yes (`enable_callbacks=true`) |
| `additional_files` | string (base64 zip) | `null` | extracted archive `≤ 10240 KB` | yes (`enable_additional_files=true`) |
| `compiler_options` | string | `null` | appended to the compile command | yes (`enable_compiler_options=true`, `allowed_languages_for_compile_options=[]` ⇒ **all** languages) |
| `command_line_arguments` | string | `null` | space-separated, appended to the run command | yes (`enable_command_line_arguments=true`) |

Notes on individual fields, all verified live:

- **`cpu_time_limit` vs `wall_time_limit`.** CPU time counts only scheduled CPU; wall time counts
  real elapsed time and catches sleeps and blocking I/O. Both produce status 5, distinguished by
  `message`:
  - CPU: `"message": "Time limit exceeded"`, `time` slightly above the limit (e.g. `"1.097"` for a limit of 1).
  - Wall: `"message": "Time limit exceeded (wall clock)"`, `wall_time` ≈ limit (e.g. `"3.1"` for a limit of 3).
- **`cpu_extra_time`** is grace added on top of `cpu_time_limit` before the process is killed. It
  does not extend what counts as "within the limit" for status purposes.
- **`number_of_runs`** re-runs the program N times and reports the **arithmetic mean** in `time`.
  Verified with `number_of_runs: 3` → `"time": "0.0156666666666667"`. Useful for stable benchmarking,
  but it multiplies worker cost by N — do not use it for grading on this 2-worker box.
- **`redirect_stderr_to_stdout: true`** merges the streams; `stderr` comes back `null`. Verified:
  a program printing to both returned `"stdout": "to stderr\nargv: ['alpha', 'beta']\n"`,
  `"stderr": null`. **Never enable this for graded runs** — it will corrupt output comparison.
- **`command_line_arguments`** is a single space-separated string. Verified: `"alpha beta"` →
  `sys.argv[1:] == ['alpha', 'beta']`.
- **`compiler_options`** substitutes into the language's `compile_cmd` (`%s` placeholder). Verified
  on C++ (id 54) with `"-DFOO -O2"` → the `#ifdef FOO` branch was taken.
- **`additional_files`** is a base64-encoded **zip**, extracted into the sandbox working directory
  alongside the source. Verified with Python (id 71): a zip containing `helper.py` and `data.txt`
  made both importable/readable, and `os.listdir('.')` returned
  `['__pycache__', 'data.txt', 'helper.py', 'run', 'script.py']`.
- **`callback_url` is not validated.** `{"callback_url": "not a url"}` was accepted and returned a
  token. Validate it yourself.
- **Language 89 ("Multi-file program")** takes no `source_code`. Put everything in
  `additional_files`, including an executable `compile` script and a `run` script. Verified working:
  a zip of `main.cpp`, `lib.cpp`, `lib.h`, plus

  ```sh
  # compile
  /usr/local/gcc-9.2.0/bin/g++ main.cpp lib.cpp -o solution
  ```
  ```sh
  # run
  LD_LIBRARY_PATH=/usr/local/gcc-9.2.0/lib64 ./solution
  ```

  returned `{"stdout": "42\n", "status": {"id": 3, "description": "Accepted"}}`.

---

## 5. `wait=true` vs `wait=false`

`wait=true` is enabled on this deployment (`enable_wait_result: true`). In the Judge0 v1.13.1
source, `wait=true` calls `IsolateJob.perform_now(submission.id)` **inline in the Rails request
thread** — the job never enters the Resque queue, and the HTTP connection is held open for the
entire compile-and-run.

### Use `wait=false` + polling for the platform. This is not a style preference.

There are `RAILS_SERVER_PROCESSES=2 × RAILS_MAX_THREADS=5` = **10 request threads on the whole
box**. Every in-flight `wait=true` call occupies one of them for the full duration of the
submission. Measured serial round-trip latency:

| Language | `wait=true` round trip (idle box) |
|---|---|
| Python 3.8.1 (71) | 0.216 – 0.222 s |
| C++ GCC 9.2.0 (54) | 0.333 – 0.336 s |
| Java OpenJDK 13 (62) | 1.44 s |

Consequences:

- **11 concurrent `wait=true` calls saturate the API server.** The 11th request queues at the HTTP
  layer; health checks, result polls, and every other API call block behind candidate code.
- A candidate submitting a `while True: pass` at the default limits holds a thread for up to
  `wall_time_limit` = **10 s**. Ten such submissions take the entire API offline for 10 seconds.
- `wait=true` also **bypasses `MAX_QUEUE_SIZE`** — the queue-full guard protects the Resque queue,
  and inline jobs never touch it. You lose your only backpressure signal.
- Any HTTP timeout (client, load balancer, reverse proxy) on a `wait=true` call loses the result
  for that client; the token is in the response body you never received.

`wait=true` is acceptable only for: manual `curl` debugging, health checks with a tiny program and
a low `cpu_time_limit`, and one-off admin scripts. Never on a candidate-facing path.

`POST /submissions/batch` **ignores `wait` entirely** — it always returns tokens immediately
(verified: `?wait=true` on a batch returned `[{"token":"f77c3bc1-..."}]` in 11 ms). This is another
reason batch is the right primitive (§7).

---

## 6. `GET /submissions/:token`

```
GET /submissions/:token?base64_encoded={true|false}&fields=<csv>
X-Auth-Token: <token>
```

- **200** with the submission object.
- **404** with an empty body if the token does not exist. Verified against
  `00000000-0000-0000-0000-000000000000`.
- **400** if the response contains bytes that are not valid UTF-8 and `base64_encoded=false`. See §8.

### Default field set

Omitting `fields` returns exactly these eight (this is `SubmissionSerializer.default_fields`):

`token`, `status`, `stdout`, `stderr`, `compile_output`, `message`, `time`, `memory`

### `fields` query parameter

Comma-separated column names, or `*` for everything. An unknown name is a **400**:

```json
{"error":"invalid fields: [bogus_field]"}
```

Subset example — `?fields=token,status_id,stdout,time,memory,exit_code` returned:

```json
{
  "stdout": "42\n",
  "status_id": 4,
  "time": "0.016",
  "memory": 3232,
  "token": "c83ed07e-1f29-4aaf-8dd7-49f068915739",
  "exit_code": 0
}
```

Note `status_id` (integer) vs `status` (object `{id, description}`) are **different fields**. Ask
for `status_id` when polling — it is smaller and easier to compare.

### Full field list (`fields=*`)

Real response from the live server:

```json
{
  "source_code": "const l=require(\"fs\").readFileSync(0,\"utf8\").trim();console.log(l.toUpperCase());",
  "language_id": 63,
  "stdin": "hello judge0",
  "expected_output": null,
  "stdout": "HELLO JUDGE0\n",
  "status_id": 3,
  "created_at": "2026-08-05T22:13:34.454Z",
  "finished_at": "2026-08-05T22:13:35.137Z",
  "time": "0.058",
  "memory": 34220,
  "stderr": null,
  "token": "da49119b-da4f-4577-8a11-97a196dc9263",
  "number_of_runs": 1,
  "cpu_time_limit": "5.0",
  "cpu_extra_time": "1.0",
  "wall_time_limit": "10.0",
  "memory_limit": 128000,
  "stack_limit": 64000,
  "max_processes_and_or_threads": 60,
  "enable_per_process_and_thread_time_limit": false,
  "enable_per_process_and_thread_memory_limit": false,
  "max_file_size": 1024,
  "compile_output": null,
  "exit_code": 0,
  "exit_signal": null,
  "message": null,
  "wall_time": "0.29",
  "compiler_options": null,
  "command_line_arguments": null,
  "redirect_stderr_to_stdout": false,
  "callback_url": null,
  "additional_files": null,
  "enable_network": false,
  "status": { "id": 3, "description": "Accepted" },
  "language": { "id": 63, "name": "JavaScript (Node.js 12.14.0)" }
}
```

### Result field types

| Field | Type | Meaning |
|---|---|---|
| `time` | string, seconds | **CPU** time, averaged over `number_of_runs`. Parse with `parseFloat`/`float`. |
| `wall_time` | string, seconds | Real elapsed time. Only present via `fields`; not in the default set. |
| `memory` | integer, **KB** | Peak memory. Equals `memory_limit` exactly when the process was OOM-killed. |
| `exit_code` | integer | Process exit status. See the signal table in §9. |
| `exit_signal` | integer or null | **Always `null` on this deployment** — see §9. |
| `stdout` / `stderr` / `compile_output` / `message` | string or null | Text streams; base64 when `base64_encoded=true`. |

### Polling frequency

`submission_cache_duration` is `1.0` s, so a GET for the same token within one second may be served
from cache. Polling a single token faster than ~1 s/req buys nothing. Use `GET /submissions/batch`
to poll many tokens in one request instead (§7).

---

## 7. Batch endpoints

`enable_batched_submissions: true`, `max_submission_batch_size: **20**`.

### `POST /submissions/batch`

```
POST /submissions/batch?base64_encoded={true|false}
Content-Type: application/json
```

Body is `{"submissions": [ <submission>, ... ]}` where each element accepts every field from §4.
Elements are independent — different languages, limits, and stdin per element are fine.

Real request:

```json
{
  "submissions": [
    {"language_id":71,"source_code":"a,b=map(int,input().split())\nprint(a+b)","stdin":"1 2","expected_output":"3"},
    {"language_id":71,"source_code":"a,b=map(int,input().split())\nprint(a+b)","stdin":"10 20","expected_output":"30"},
    {"language_id":71,"source_code":"a,b=map(int,input().split())\nprint(a+b)","stdin":"-5 5","expected_output":"1"}
  ]
}
```

Real response (**201 Created**) — an array, **positionally aligned with the request**:

```json
[
  {"token":"23792b7d-b382-49e1-8ccf-5e3b79075af2"},
  {"token":"16f8fda8-6655-4844-8855-ab3719425f67"},
  {"token":"7c6e7861-ae3d-440c-a0f9-8689b8b35362"}
]
```

Exceeding 20 is a **400** (verified with 21 elements); **nothing is enqueued**:

```json
{"error":"number of submissions in a batch should be less than or equal to 20"}
```

If an individual element fails validation, its array slot contains the error object instead of a
token — always check for `token` before using a slot.

### `GET /submissions/batch?tokens=...`

Comma-separated tokens, up to 20. Same `base64_encoded` and `fields` parameters as the single GET.

```bash
curl -s -H "X-Auth-Token: $JUDGE0_TOKEN" \
  "$JUDGE0_URL/submissions/batch?tokens=$T1,$T2,$T3&base64_encoded=false&fields=token,status_id,stdout,time,memory"
```

Real response:

```json
{
  "submissions": [
    {"stdout":"3\n",  "status_id":3, "time":"0.025", "memory":3884, "token":"23792b7d-b382-49e1-8ccf-5e3b79075af2"},
    {"stdout":"30\n", "status_id":3, "time":"0.022", "memory":3640, "token":"16f8fda8-6655-4844-8855-ab3719425f67"},
    {"stdout":"0\n",  "status_id":4, "time":"0.015", "memory":3264, "token":"7c6e7861-ae3d-440c-a0f9-8689b8b35362"}
  ]
}
```

Note the response is an **object** with a `submissions` key, unlike the POST which returns a bare
array. Unknown tokens come back as `null` in position — verified:

```json
{"submissions":[{"status_id":3,"token":"23792b7d-b382-49e1-8ccf-5e3b79075af2"},null]}
```

Always null-check each slot.

### Why batch is the right primitive for grading

One candidate solution against N hidden test cases = N submissions that share `source_code` and
`language_id` and differ only in `stdin`/`expected_output`.

- **1 HTTP round trip** to enqueue all N instead of N round trips. Measured: 20 individual async
  POSTs took 0.53 s of client time; one batch POST of 20 takes ~10 ms.
- **1 HTTP round trip per poll cycle** to check all N statuses, instead of N. This matters more
  than the create saving — with 0.5 s polling over a 7 s job, batch polling is 14 requests instead
  of 280.
- Judge0 checks `MAX_QUEUE_SIZE` **once for the whole batch** (`queue.size + number_of_submissions
  > MAX_QUEUE_SIZE`), so a batch is all-or-nothing: you never get half a test suite enqueued and
  half rejected.
- Each element still runs in its own isolated sandbox with its own limits and its own status, so
  per-test-case scoring and per-test-case time/memory reporting come out for free.

If a problem has more than 20 test cases, split into chunks of 20 and treat the chunks as one
logical grading job.

---

## 8. `base64_encoded=true`

When `true`, these request fields must be base64: `source_code`, `stdin`, `expected_output`,
`additional_files`. These response fields come back base64: `source_code`, `stdin`,
`expected_output`, `stdout`, `stderr`, `compile_output`, `message`, `additional_files`.

Judge0 wraps base64 output at 60 characters with embedded `\n` — standard base64 decoders handle
this, but a strict/validating decoder will not. Strip whitespace before decoding if your decoder
complains.

### When base64 is mandatory

**It is mandatory whenever the response may contain non-UTF-8 bytes.** With
`base64_encoded=false`, such a response is a hard **400** and you lose the result of that request:

```json
{
  "error": "some attributes for this submission cannot be converted to UTF-8, use base64_encoded=true query parameter",
  "token": "2016a8b3-afd5-4a4c-80b3-935d3abf6c05"
}
```

This is **not** a hypothetical. The transcript above is a real `wait=true` response from this
server for a trivial C++ compile error:

```cpp
int main(){ retrn 0; }
```

GCC 9.2.0 emits Unicode smart quotes (`‘retrn’`) in diagnostics, and the whole response fails
UTF-8 conversion. Re-fetching the same token with `base64_encoded=true` works:

```json
{
  "stdout": null, "time": null, "memory": null, "stderr": null,
  "token": "2016a8b3-afd5-4a4c-80b3-935d3abf6c05",
  "compile_output": "bWFpbi5jcHA6IEluIGZ1bmN0aW9uIOKAmGludCBtYWluKCnigJk6Cm1haW4u\nY3BwOjE6MTM6IGVycm9yOiDigJhyZXRybuKAmSB3YXMgbm90IGRlY2xhcmVk\nIGluIHRoaXMgc2NvcGUKICAgIDEgfCBpbnQgbWFpbigpeyByZXRybiAwOyB9\nCiAgICAgIHwgICAgICAgICAgICAgXn5+fn4K\n",
  "message": null,
  "status": { "id": 6, "description": "Compilation Error" }
}
```

Decoded `compile_output`:

```
main.cpp: In function ‘int main()’:
main.cpp:1:13: error: ‘retrn’ was not declared in this scope
    1 | int main(){ retrn 0; }
      |             ^~~~~
```

Cases that require `base64_encoded=true`:

1. **Any C/C++/Objective-C compile error** — GCC and Clang use U+2018/U+2019 quotes. Unavoidable.
2. **Binary or non-UTF-8 program output** — candidate code writing raw bytes to stdout.
3. **Source code containing characters that break JSON round-tripping** in your stack, or source
   the platform must transport byte-exactly (trailing whitespace, tabs, CRLF, BOM, `\0`).
4. **Test-case data with non-UTF-8 bytes**, or stdin/expected_output where exact bytes matter.
5. Judge0 is also lenient about *invalid* base64 in requests: `"!!!not base64!!!"` was accepted and
   decoded to garbage bytes, producing a `Runtime Error (NZEC)` with a Python
   "Non-UTF-8 code starting with '\x9e'" traceback rather than a 400. **Judge0 will not tell you
   your base64 was malformed.** Encode correctly and validate on your side.

### Recommendation

**Set `base64_encoded=true` on every request and response in the platform, unconditionally.** The
cost is ~33 % payload growth. The alternative is a class of 400s that appear only on the exact
inputs (compile errors, weird bytes) where the candidate most needs to see the output.

### Encoding and decoding

JavaScript (Node 18+ and browsers), UTF-8 safe:

```js
const enc = (s) => Buffer.from(s, "utf8").toString("base64");
const dec = (b) => (b == null ? null : Buffer.from(b, "base64").toString("utf8"));

// Browser / non-Node (btoa is Latin-1 only — you MUST go through TextEncoder):
const encB = (s) => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
const decB = (b) =>
  b == null ? null
            : new TextDecoder().decode(Uint8Array.from(atob(b), (c) => c.charCodeAt(0)));
```

> `btoa("café")` throws `InvalidCharacterError`. Never call `btoa` on source code directly.

Python:

```python
import base64

def enc(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")

def dec(b: str | None) -> str | None:
    if b is None:
        return None
    # errors="replace" — candidate output is not guaranteed to be valid UTF-8
    return base64.b64decode(b).decode("utf-8", errors="replace")
```

Use `errors="replace"` (or the JS equivalent, which `TextDecoder` does by default). A candidate
program *can* emit invalid UTF-8; the whole point of base64 mode is that this must not crash your
grader.

---

## 9. Status codes and result interpretation

`GET /statuses`, verbatim from the live server:

```json
[{"id":1,"description":"In Queue"},{"id":2,"description":"Processing"},{"id":3,"description":"Accepted"},{"id":4,"description":"Wrong Answer"},{"id":5,"description":"Time Limit Exceeded"},{"id":6,"description":"Compilation Error"},{"id":7,"description":"Runtime Error (SIGSEGV)"},{"id":8,"description":"Runtime Error (SIGXFSZ)"},{"id":9,"description":"Runtime Error (SIGFPE)"},{"id":10,"description":"Runtime Error (SIGABRT)"},{"id":11,"description":"Runtime Error (NZEC)"},{"id":12,"description":"Runtime Error (Other)"},{"id":13,"description":"Internal Error"},{"id":14,"description":"Exec Format Error"}]
```

| id | description | Terminal | Which field carries the detail | Show the candidate |
|---:|---|:---:|---|---|
| 1 | In Queue | no | — | "Queued…" spinner |
| 2 | Processing | no | — | "Running…" spinner |
| 3 | Accepted | yes | `stdout`, `time`, `memory` | Test passed. Show time/memory. |
| 4 | Wrong Answer | yes | `stdout` vs your `expected_output` | Test failed. Sample cases: show expected vs actual diff. Hidden cases: show "Wrong Answer" only. |
| 5 | Time Limit Exceeded | yes | `message` = `"Time limit exceeded"` (CPU) or `"Time limit exceeded (wall clock)"` | "Time limit exceeded". Never show partial `stdout` (it is truncated at kill time). |
| 6 | **Compilation Error** | yes | **`compile_output`** — `stdout`/`stderr`/`time`/`memory` are all `null` | Show `compile_output` **in full, for hidden test cases too**. It contains no test data. |
| 7 | Runtime Error (SIGSEGV) | yes | `message`, `stderr` | "Runtime error (segmentation fault)" |
| 8 | Runtime Error (SIGXFSZ) | yes | `message`, `stderr` | "Runtime error (output/file size limit)" |
| 9 | Runtime Error (SIGFPE) | yes | `message`, `stderr` | "Runtime error (arithmetic)" |
| 10 | Runtime Error (SIGABRT) | yes | `message`, `stderr` | "Runtime error (aborted)" |
| 11 | Runtime Error (NZEC) | yes | **`stderr`** + `message` + `exit_code` | Show `stderr` for sample cases; for hidden cases show the exception class/line but consider redacting text that could leak input. |
| 12 | Runtime Error (Other) | yes | `message` | "Runtime error" |
| 13 | Internal Error | yes | `message` | **Your bug or an infrastructure failure, not the candidate's.** Do not score it. Retry once, then alert. |
| 14 | Exec Format Error | yes | `message` | Only reachable via language 44 (Executable) with a non-executable payload. |

### Critical finding: statuses 7–10 and 12 are effectively unreachable on this deployment

Judge0 always executes the program through a generated `run` shell script. The shell absorbs the
signal, exits with `128 + signum`, and isolate therefore reports a normal non-zero exit rather than
a signal. Every abnormal termination lands on **status 11 (NZEC)** with `exit_signal: null`.

Verified live, all four returning status 11:

| Program | `exit_code` | `exit_signal` | `status.id` | `stderr` (decoded) |
|---|---:|---|---:|---|
| C: `int main(){int*p=0;*p=1;}` | 139 | `null` | **11** | `run: line 1: 3 Segmentation fault (core dumped) ./a.out` |
| C: `int a=1,b=0; return a/b;` | 136 | `null` | **11** | (empty) |
| C++: `throw std::runtime_error("x")` | 134 | `null` | **11** | (empty) |
| Python: 200 MB alloc under `memory_limit: 32000` | 137 | `null` | **11** | `run: line 1: 3 Killed /usr/local/python-3.8.1/bin/python3 script.py` |

**Do not branch on status ids 7–10 or 12.** Classify runtime failures from `exit_code` instead:

| `exit_code` | Signal | Present to the candidate as |
|---:|---|---|
| 1, 2, … 125 | — | Program exited with a non-zero status. Show `stderr`. |
| 134 | 128+6 SIGABRT | Aborted (uncaught C++ exception, `assert`, `abort()`) |
| 136 | 128+8 SIGFPE | Arithmetic error (integer division by zero) |
| 137 | 128+9 SIGKILL | **Out of memory** — killed. Compare `memory` against `memory_limit`; they will be equal. |
| 139 | 128+11 SIGSEGV | Segmentation fault (null deref, stack overflow, out-of-bounds) |

**Memory-limit exceeded has no dedicated status.** It surfaces as status 11 / `exit_code: 137` with
`memory == memory_limit`. Detect it explicitly:

```python
mle = (r["status"]["id"] == 11 and r.get("exit_code") == 137
       and r.get("memory") is not None and r["memory"] >= memory_limit)
```

Note also that exceeding `max_file_size` did **not** produce SIGXFSZ (status 8) on this box — the
Python runtime caught the signal and raised `OSError: [Errno 27] File too large`, giving status 11
with `exit_code: 1`. Behaviour will vary by language.

### `stderr` vs `compile_output` vs `message`

These are three distinct fields with three distinct producers. Never concatenate them blindly.

| Field | Produced by | Populated when | Contains |
|---|---|---|---|
| `compile_output` | the **compiler**, before the program ever runs | `status.id == 6`, or on a warning-producing successful compile | Compiler diagnostics. **Never contains test-case data** — always safe to show in full, even for hidden test cases. |
| `stderr` | the **candidate's program** | any run that wrote to fd 2 | Tracebacks, panics, the program's own logging, and the `run:` shell wrapper's message on a signal. **May echo the test input** — redact for hidden test cases. |
| `message` | **Judge0 itself** | TLE, non-zero exit, internal errors | Judge-level explanation: `"Time limit exceeded"`, `"Time limit exceeded (wall clock)"`, `"Exited with error status 139"`. Never candidate-controlled; always safe to show. |

Rendering rule:

```
if status.id == 6:            show compile_output              (always safe)
elif status.id == 5:          show message                     (always safe)
elif status.id >= 7:          show message + (stderr if the test case is a sample)
elif status.id == 4:          show stdout vs expected          (only if the test case is a sample)
else:                         show stdout, time, memory
```

---

## 10. Output comparison semantics

Judge0 sets `Accepted` vs `Wrong Answer` with this comparison (v1.13.1 `isolate_job.rb`):

```ruby
def strip(text)
  return nil unless text
  text.split("\n").collect(&:rstrip).join("\n").rstrip
end
# ...
elsif submission.expected_output.nil? || strip(submission.expected_output) == strip(submission.stdout)
  return Status.ac
else
  return Status.wa
```

That is: **right-strip every line, then right-strip the whole string, then compare exactly.**
Verified live:

| stdout | expected_output | Result |
|---|---|---|
| `"3\n"` | `"3"` | ✅ Accepted — trailing newline ignored |
| `"3"` | `"3\n"` | ✅ Accepted — symmetric |
| `"3 \n"` | `"3"` | ✅ Accepted — trailing space stripped |
| `"a \nb\n"` | `"a\nb"` | ✅ Accepted — per-line right-strip |
| `"3\r\n"` | `"3"` | ✅ Accepted — CR is trailing whitespace |
| `" 3\n"` | `"3"` | ❌ **Wrong Answer** — **leading** whitespace is significant |

Implications for the platform:

- **Leading whitespace and indentation are significant.** Blank-line and inter-token spacing inside
  a line are significant. Only *trailing* whitespace is forgiven.
- **If `expected_output` is omitted or `null`, any run that exits 0 is `Accepted`.** Verified:
  `{"language_id":71,"source_code":"print(9)"}` returned status 3. This is a silent-pass trap — if
  a bug in your grader drops `expected_output`, every hidden test case passes. **Assert
  `expected_output` is non-null before submitting a graded run.**
- Judge0 offers **no** float tolerance, no unordered comparison, no token-based comparison. For
  problems needing those, submit **without** `expected_output`, treat status 3 as "ran cleanly",
  and compare `stdout` yourself in the platform backend.

---

## 11. Languages (all 47)

`GET /languages` returns 47 active languages. (`GET /languages/all` returns 89 including archived
ones; **archived language ids are rejected by `POST /submissions`** — use the 47 below.)

`Source file` is the filename the code is written to inside the sandbox — it dictates the required
class/module name in Java, C#, Kotlin, Scala, Swift, and VB.Net. Retrieved per-language from
`GET /languages/:id`.

| ID | Name | Source file | Compiled |
|---:|---|---|:---:|
| 43 | Plain Text | `text.txt` | no |
| 44 | Executable | `a.out` | no |
| 45 | Assembly (NASM 2.14.02) | `main.asm` | yes |
| 46 | Bash (5.0.0) | `script.sh` | no |
| 47 | Basic (FBC 1.07.1) | `main.bas` | yes |
| 48 | C (GCC 7.4.0) | `main.c` | yes |
| 49 | C (GCC 8.3.0) | `main.c` | yes |
| 50 | C (GCC 9.2.0) | `main.c` | yes |
| 51 | C# (Mono 6.6.0.161) | `Main.cs` | yes |
| 52 | C++ (GCC 7.4.0) | `main.cpp` | yes |
| 53 | C++ (GCC 8.3.0) | `main.cpp` | yes |
| 54 | C++ (GCC 9.2.0) | `main.cpp` | yes |
| 55 | Common Lisp (SBCL 2.0.0) | `script.lisp` | no |
| 56 | D (DMD 2.089.1) | `main.d` | yes |
| 57 | Elixir (1.9.4) | `script.exs` | no |
| 58 | Erlang (OTP 22.2) | `main.erl` | no |
| 59 | Fortran (GFortran 9.2.0) | `main.f90` | yes |
| 60 | Go (1.13.5) | `main.go` | yes |
| 61 | Haskell (GHC 8.8.1) | `main.hs` | yes |
| 62 | Java (OpenJDK 13.0.1) | `Main.java` | yes |
| 63 | JavaScript (Node.js 12.14.0) | `script.js` | no |
| 64 | Lua (5.3.5) | `script.lua` | yes |
| 65 | OCaml (4.09.0) | `main.ml` | yes |
| 66 | Octave (5.1.0) | `script.m` | no |
| 67 | Pascal (FPC 3.0.4) | `main.pas` | yes |
| 68 | PHP (7.4.1) | `script.php` | no |
| 69 | Prolog (GNU Prolog 1.4.5) | `main.pro` | yes |
| 70 | Python (2.7.17) | `script.py` | no |
| 71 | Python (3.8.1) | `script.py` | no |
| 72 | Ruby (2.7.0) | `script.rb` | no |
| 73 | Rust (1.40.0) | `main.rs` | yes |
| 74 | TypeScript (3.7.4) | `script.ts` | yes |
| 75 | C (Clang 7.0.1) | `main.c` | yes |
| 76 | C++ (Clang 7.0.1) | `main.cpp` | yes |
| 77 | COBOL (GnuCOBOL 2.2) | `main.cob` | yes |
| 78 | Kotlin (1.3.70) | `Main.kt` | yes |
| 79 | Objective-C (Clang 7.0.1) | `main.m` | yes |
| 80 | R (4.0.0) | `script.r` | no |
| 81 | Scala (2.13.2) | `Main.scala` | yes |
| 82 | SQL (SQLite 3.27.2) | `script.sql` | no |
| 83 | Swift (5.2.3) | `Main.swift` | yes |
| 84 | Visual Basic.Net (vbnc 0.0.0.5943) | `Main.vb` | yes |
| 85 | Perl (5.28.1) | `script.pl` | no |
| 86 | Clojure (1.10.1) | `main.clj` | no |
| 87 | F# (.NET Core SDK 3.1.202) | `script.fsx` | no |
| 88 | Groovy (3.0.3) | `script.groovy` | yes |
| 89 | Multi-file program | — | no |

**Total: 47.**

Per-language notes that will bite a platform builder:

- **Java (62)**: the file is `Main.java`, so the public class **must** be named `Main`.
  `run_cmd` is `java Main`. Same pattern for C# (`Main.cs`), Kotlin (`MainKt` — the file is
  `Main.kt` so a top-level `main()` compiles to class `MainKt`), Scala (`Main.scala`), Swift
  (`Main.swift`), VB.Net (`Main.vb`). Your starter templates must hardcode these names.
- **Java is ~1.4 s per submission** end to end (JVM startup dominates) versus ~0.22 s for Python.
  A 10-test-case Java grading job costs roughly 7 s of worker time on this box. Budget accordingly (§16).
- **SQL (82)**: `run_cmd` is `cat script.sql | sqlite3 db.sqlite` — the submission runs against a
  fresh empty SQLite database. Ship the schema and seed data via `additional_files` plus a
  `.read` directive, or via `stdin` is not available here (stdin is consumed by the pipe).
- **Plain Text (43)**: `cat text.txt`. Useful as a zero-cost "echo" health check.
- **Executable (44)**: `chmod +x a.out && ./a.out`. Accepts a base64 binary as `source_code`. This
  runs an arbitrary uploaded binary — **block language 44 in the platform's language allowlist**.
- **Multi-file program (89)**: see §4.
- **Recommended candidate-facing allowlist**: 50 (C), 54 (C++), 62 (Java), 63 (JavaScript),
  71 (Python 3), 72 (Ruby), 73 (Rust), 60 (Go), 51 (C#), 74 (TypeScript), 78 (Kotlin), 68 (PHP),
  83 (Swift), 81 (Scala). Exclude 43, 44, 89 and the older duplicate compiler versions.

---

## 12. Callbacks

`enable_callbacks: true`, `callbacks_max_tries: 3`, `callbacks_timeout: 5.0`.

Set `callback_url` on a submission and Judge0 notifies you when it reaches a terminal status,
instead of you polling.

Wire format, from the Judge0 v1.13.1 source (`app/jobs/isolate_job.rb` — **this could not be
observed from outside the box, since the callback originates from the Judge0 host**):

```ruby
def call_callback
  return unless submission.callback_url.present?

  serialized_submission = ActiveModelSerializers::SerializableResource.new(
    submission,
    {
      serializer: SubmissionSerializer,
      base64_encoded: true,
      fields: SubmissionSerializer.default_fields
    }
  ).to_json

  Config::CALLBACKS_MAX_TRIES.times do
    begin
      response = HTTParty.put(
        submission.callback_url,
        body: serialized_submission,
        headers: { "Content-Type" => "application/json" },
        timeout: Config::CALLBACKS_TIMEOUT
      )
      break
    rescue Exception => e
    end
  end
rescue Exception => e
end
```

Therefore:

| Property | Value |
|---|---|
| HTTP method | **`PUT`** (not POST) |
| `Content-Type` | `application/json` |
| Body | The submission serialized with **`base64_encoded: true`**, always |
| Body fields | The default field set only: `token`, `status`, `stdout`, `stderr`, `compile_output`, `message`, `time`, `memory` |
| Retries | Up to `callbacks_max_tries` = **3** |
| Timeout | `callbacks_timeout` = **5.0 s** per attempt |
| Auth | **None.** Judge0 sends no auth header. |
| Failure handling | All exceptions swallowed. A permanently failing callback is **silently dropped**. |

Verified live: a submission with `callback_url` set completes normally regardless of whether the
callback can be delivered (`"callback_url": "http://127.0.0.1:9/judge0-callback"` → status 3,
`finished_at` set). `callback_url` is **not validated** — `"not a url"` was accepted.

### Should the platform use callbacks?

**Use polling as the primary mechanism; treat callbacks as an optional latency optimisation.**

Reasons:

- **No authentication and no signature.** Anyone who can reach your callback endpoint can forge a
  result. If you use callbacks, put a long unguessable nonce in the URL path
  (`/judge0/cb/<128-bit-random>`), look the token up by that nonce, and **still re-fetch
  `GET /submissions/:token` to confirm** before scoring. Never trust the callback body as
  authoritative.
- **Delivery is best-effort with no dead-letter.** Three tries, 5 s each, all errors swallowed. A
  30-second deploy of your backend loses every result in that window, permanently and silently.
- **Your endpoint must be reachable from `65.0.29.135`** over plain HTTP. That means a public
  ingress, which is a new attack surface.
- **The body carries only the default eight fields.** No `exit_code`, which §9 shows you need to
  classify runtime errors. You would have to re-fetch anyway.
- Polling costs almost nothing here: one batched GET per grading job per interval.

If you do use callbacks, keep a reconciliation sweep that polls any submission still non-terminal
after ~30 s. That sweep is the real correctness guarantee, so you may as well make it the primary
path.

---

## 13. Error responses

| Code | When | Body |
|---:|---|---|
| **400** | Non-UTF-8 result with `base64_encoded=false` | `{"error":"some attributes for this submission cannot be converted to UTF-8, use base64_encoded=true query parameter","token":"..."}` |
| **400** | Unknown name in `fields` | `{"error":"invalid fields: [bogus_field]"}` |
| **400** | Batch larger than 20 | `{"error":"number of submissions in a batch should be less than or equal to 20"}` |
| **400** | `wait=true` when disabled (not the case here) | `{"error":"wait not allowed"}` |
| **401** | Missing or wrong `X-Auth-Token` | empty, `Content-Type: text/html` |
| **403** | `GET /submissions` (list), `POST /authorize`, `DELETE /submissions/:token` | empty, `Content-Type: text/html` |
| **404** | Unknown submission token | empty body |
| **422** | Validation failure on `POST /submissions` | `{"<field>": ["<message>", ...]}` |
| **503** | Queue full | `{"error":"queue is full"}` |

### 422 validation

The body is a map of field name → array of messages. Real responses from the live server:

```json
{"language_id":["can't be blank","language with id  doesn't exist"]}
```
```json
{"language_id":["language with id 9999 doesn't exist"]}
```
```json
{"cpu_time_limit":["must be less than or equal to 15.0"]}
```

All limit violations in one request are reported together:

```json
{
  "number_of_runs": ["must be less than or equal to 20"],
  "cpu_extra_time": ["must be less than or equal to 5.0"],
  "wall_time_limit": ["must be less than or equal to 20.0"],
  "memory_limit": ["must be less than or equal to 512000"],
  "stack_limit": ["must be less than or equal to 128000"],
  "max_processes_and_or_threads": ["must be less than or equal to 120"],
  "max_file_size": ["must be less than or equal to 4096"]
}
```

**422 is never retryable** — it is a bug in your request. Log it, fail the grading job, alert. Do
not put it in a retry loop.

### 503 — queue full

`MAX_QUEUE_SIZE=500`. Judge0 checks, in `submissions_controller.rb`:

```ruby
if Resque.size(ENV["JUDGE0_VERSION"]) + number_of_submissions > Config::MAX_QUEUE_SIZE
  render json: { error: "queue is full" }, status: :service_unavailable
```

Key properties:

- The check counts **the whole batch at once**, so a batch is atomic: either all 20 are enqueued or
  none are. You never get a partially-graded test suite.
- `wait=true` submissions **bypass this check entirely** (they never enter the queue). Another
  reason not to use `wait=true` (§5).
- 500 queued items at the measured 2.78 submissions/sec is a **~3-minute backlog**. If you are
  seeing 503, the box has already been unusable for minutes. Do not treat 503 as your primary
  backpressure signal.
- **Poll `GET /workers` and read `size`** — that is the live queue depth (verified: it read 8, 8, 6
  while 10 one-second submissions drained). Apply backpressure at a threshold far below 500, e.g.
  refuse to enqueue new grading jobs when `size > 100`.

503 **is** retryable, with exponential backoff and jitter, and should surface to the candidate as
"the judge is busy, retrying" rather than a failure.

---

## 14. Enforced limits (`GET /config_info`)

Verbatim from the live server. **These are authoritative; anything else in this document defers to
this block.**

```json
{
  "maintenance_mode": false,
  "enable_wait_result": true,
  "enable_compiler_options": true,
  "allowed_languages_for_compile_options": [],
  "enable_command_line_arguments": true,
  "enable_submission_delete": false,
  "enable_callbacks": true,
  "callbacks_max_tries": 3,
  "callbacks_timeout": 5.0,
  "enable_additional_files": true,
  "max_queue_size": 500,
  "cpu_time_limit": 5.0,
  "max_cpu_time_limit": 15.0,
  "cpu_extra_time": 1.0,
  "max_cpu_extra_time": 5.0,
  "wall_time_limit": 10.0,
  "max_wall_time_limit": 20.0,
  "memory_limit": 128000,
  "max_memory_limit": 512000,
  "stack_limit": 64000,
  "max_stack_limit": 128000,
  "max_processes_and_or_threads": 60,
  "max_max_processes_and_or_threads": 120,
  "enable_per_process_and_thread_time_limit": false,
  "allow_enable_per_process_and_thread_time_limit": true,
  "enable_per_process_and_thread_memory_limit": false,
  "allow_enable_per_process_and_thread_memory_limit": true,
  "max_file_size": 1024,
  "max_max_file_size": 4096,
  "number_of_runs": 1,
  "max_number_of_runs": 20,
  "redirect_stderr_to_stdout": false,
  "max_extract_size": 10240,
  "enable_batched_submissions": true,
  "max_submission_batch_size": 20,
  "submission_cache_duration": 1.0,
  "use_docs_as_homepage": false,
  "allow_enable_network": true,
  "enable_network": false
}
```

Values not stated in the deployment brief but enforced here:

| Setting | Value | Why it matters |
|---|---|---|
| `max_submission_batch_size` | **20** | Hard cap on both batch POST and `?tokens=` GET. Chunk larger test suites. |
| `stack_limit` | 64000 KB (max 128000) | Deep recursion hits this before `memory_limit`. Raise it for tree/graph problems. |
| `cpu_extra_time` | 1.0 s (max 5.0) | Grace period on top of `cpu_time_limit`. |
| `max_processes_and_or_threads` | 60 (max 120) | Java/Go runtimes spawn threads; 60 is comfortable, but a fork bomb hits this and gets NZEC. |
| `max_extract_size` | 10240 KB | Ceiling on the *extracted* size of `additional_files`. |
| `enable_submission_delete` | false | `DELETE` is 403. Submissions accumulate forever — see §16. |
| `allow_enable_network` | **true** | Clients may set `enable_network: true`. **Security-relevant — see §15.** |
| `submission_cache_duration` | 1.0 s | GET responses cached ~1 s; don't poll a single token faster. |

---

## 15. Building a HackerRank-style platform

### 15.1 Modelling a problem

```
Problem
  id, slug, title, statement_md
  allowed_language_ids  int[]        -- allowlist; exclude 43, 44, 89
  time_limit_ms         int          -- your semantic limit, per language
  memory_limit_kb       int
  starter_code          map<lang_id, text>
  reference_solution    map<lang_id, text>   -- used to validate test cases at authoring time

TestCase
  id, problem_id
  ordinal          int
  kind             enum('sample','hidden')
  stdin            bytea       -- store raw bytes, base64 only at the API boundary
  expected_stdout  bytea
  weight           int         -- for partial scoring
  group_id         int null    -- subtask grouping (optional)

Attempt                          -- one candidate pressing "Submit"
  id, candidate_id, problem_id, language_id
  source_code      text
  state            enum('queued','running','done','error')
  score            int
  created_at, finished_at

AttemptRun                       -- one Judge0 submission == one test case
  id, attempt_id, test_case_id
  judge0_token     uuid  UNIQUE NOT NULL   -- index this
  status_id        int   null
  exit_code        int   null
  stdout, stderr, compile_output, message  -- store raw
  time_s           numeric null
  memory_kb        int null
  polled_at        timestamptz
```

`judge0_token` must be `UNIQUE NOT NULL` and written **in the same transaction** that records the
run. Judge0 offers no way to list or search submissions (403 on `GET /submissions`, §2) — if you
lose a token, the result is unrecoverable.

### 15.2 How to grade: one submission per test case

**Recommendation: one Judge0 submission per test case, dispatched in batches of up to 20. Do not
concatenate test cases into a single run.**

| | Batch-per-test-case (**recommended**) | Single run, concatenated stdin |
|---|---|---|
| Per-test verdict | Yes — each has its own `status` | No — one verdict for everything |
| Partial scoring | Natural | Impossible without parsing output |
| Per-test time/memory | Yes | Aggregate only |
| Time limit semantics | Correct: limit applies per test | Wrong: the limit must cover N tests, so one slow test is indistinguishable from N fast ones |
| Isolation | Each test in a fresh sandbox | State (globals, files, RNG) leaks between tests |
| First test crashes | Remaining tests still run | Everything after is lost |
| Requires cooperating candidate code | No | **Yes** — the solution must loop over N cases, which changes the problem |
| Worker cost | N × (startup + run) | 1 × startup + N × run |
| API calls | 1 create + ~k polls per 20 tests | 1 create + ~k polls |

The only real advantage of concatenation is amortising process startup. On this box that is
~0.2 s (Python) to ~1.4 s (Java) per submission — meaningful for Java but not worth losing
per-test verdicts, per-test time limits, and sandbox isolation, all of which a HackerRank-style UI
requires.

Compromise for Java/JVM problems if worker time becomes the bottleneck: run **sample** test cases
individually (candidates need per-test feedback there), and group **hidden** test cases into
subtask bundles of 3–5 concatenated cases, scoring each bundle all-or-nothing.

### 15.3 Grading flow

```
1. Compile check (optional but recommended, 1 submission):
     POST /submissions with the source, no stdin, cpu_time_limit=1
     if status.id == 6  -> fail the whole attempt immediately,
                           show compile_output, skip all test cases.
     This saves N-1 submissions on every compile error, which is the
     single most common failure mode.

2. Chunk the test cases into groups of <= 20.

3. For each chunk:
     POST /submissions/batch  -> array of tokens
     store tokens against AttemptRun rows (same transaction)

4. Poll GET /submissions/batch?tokens=<up to 20>&base64_encoded=true
     &fields=token,status_id,stdout,stderr,compile_output,message,time,memory,exit_code
   until every slot has status_id > 2 (see backoff in 15.7).

5. Score, then mark the attempt done.
```

Step 1 is worth its cost: a compile error otherwise burns N worker slots producing N identical
`compile_output` values.

### 15.4 Enforcing per-problem time and memory limits

Send limits explicitly on every submission — never rely on the server defaults, which are
deployment config and can change under you.

```json
{
  "cpu_time_limit": 2.0,
  "wall_time_limit": 5.0,
  "memory_limit": 262144,
  "stack_limit": 128000,
  "number_of_runs": 1,
  "redirect_stderr_to_stdout": false,
  "enable_network": false
}
```

Rules:

- **Always set `wall_time_limit` ≥ 2 × `cpu_time_limit`** (and at least `cpu_time_limit + 3`). Wall
  time includes compile time, JVM/interpreter startup, and scheduling delay from the other worker.
  A wall limit that is too tight produces spurious TLEs under load — a fairness bug that is very
  hard to debug after the fact.
- **Scale `cpu_time_limit` by language.** Calibrate against your reference solution: set the limit
  to roughly 3–5× the reference solution's measured `time` for that language. A single limit across
  C++ and Python makes most Python solutions unsolvable.

  Suggested multipliers relative to a C/C++ baseline: C/C++/Rust/Go ×1, Java/C#/Kotlin/Scala/Swift
  ×2, JavaScript/TypeScript ×2, Python/Ruby/PHP/Perl ×3–5, R/Octave ×5.
- **Ceilings are hard:** `cpu_time_limit ≤ 15`, `wall_time_limit ≤ 20`, `memory_limit ≤ 512000` KB,
  `stack_limit ≤ 128000` KB. Exceeding them is a 422, not a clamp.
- **`memory_limit` is per submission, and the box has 913 MiB total with 2 workers.** Two concurrent
  submissions at the 512000 KB maximum is 1 GB and will push the host into swap. **Cap
  `memory_limit` at 256000 KB (250 MB) in the platform** unless a specific problem genuinely needs
  more.
- **Raise `stack_limit` to 128000 for recursion-heavy problems** (DFS, tree traversal). The default
  64000 KB causes a stack-overflow SIGSEGV that surfaces as the confusing status 11 /
  `exit_code: 139` (§9).
- Keep `number_of_runs: 1`. Anything higher multiplies worker cost on a 2-worker box.
- Never set `redirect_stderr_to_stdout: true` on a graded run — debug prints on stderr would be
  merged into stdout and fail the comparison.

### 15.5 Preventing `enable_network` abuse

**`allow_enable_network` is `true` on this deployment, and it works.** Verified live:

```jsonc
// {"language_id":71,"source_code":"...socket.create_connection(('8.8.8.8',53))...",
//  "enable_network": false}   (default)
{"stdout":"NET BLOCKED OSError\n","status":{"id":3,"description":"Accepted"}}

// same source with "enable_network": true
{"stdout":"NET OK\n","status":{"id":3,"description":"Accepted"}}
```

With `enable_network: true`, candidate code has **real outbound internet access** from inside your
VPC. A candidate could exfiltrate the problem statement and hidden test inputs, or call an LLM API
to solve the problem.

Required mitigations, in order:

1. **Strip `enable_network` from every candidate-originated payload in the backend proxy.** Do not
   pass it through, do not honour it if present. Build the Judge0 request from a server-side
   allowlist of fields — never forward a client-supplied JSON body to Judge0.
2. **Explicitly send `"enable_network": false`** on every submission rather than relying on the
   default. The default is deployment config that could change.
3. The same allowlist must strip `callback_url` (SSRF: Judge0 will `PUT` to any URL a candidate
   supplies, from inside your network), `additional_files`, `compiler_options`,
   `command_line_arguments`, and every `*_limit` field. The candidate controls exactly two things:
   `source_code` and `language_id`.
4. **Never expose Judge0 to candidate browsers.** See §19.
5. At the infrastructure level, apply an egress security-group/firewall rule on `65.0.29.135` that
   denies outbound traffic except to what Judge0 itself needs. This is the only mitigation that
   survives a bug in your proxy.

### 15.6 Partial scoring

Score from per-test statuses:

```python
def score_attempt(runs, test_cases):
    by_id = {tc.id: tc for tc in test_cases}
    total = sum(tc.weight for tc in test_cases)

    # A compile error fails the whole attempt regardless of test cases.
    if any(r.status_id == 6 for r in runs):
        return 0, "compilation_error"

    # Internal Error is our fault, not the candidate's: do not score it.
    if any(r.status_id == 13 for r in runs):
        raise NeedsRegrade()

    earned = sum(by_id[r.test_case_id].weight for r in runs if r.status_id == 3)
    return round(100 * earned / total), "scored"
```

Guidance:

- **Weight hidden test cases; give sample test cases weight 0.** Samples exist for feedback, and
  their inputs are public.
- **Status 13 (Internal Error) must never cost the candidate points.** Re-submit that test case
  once; if it fails again, flag the attempt for manual review.
- **Subtask/group scoring** (all-or-nothing per group) matches HackerRank's model better than
  per-test partial credit for problems where a group shares a constraint bound. Use `group_id`.
- Keep a **first failing hidden test ordinal** on the attempt — it is the single most useful piece
  of internal debugging data, and it must never be shown to the candidate.
- Report aggregate `time` and `memory` as the **maximum across passing runs**, not the mean. That
  is what "your solution runs in X ms" means to a candidate.

### 15.7 Polling strategy

Constraints: `submission_cache_duration` is 1.0 s, batch GET handles 20 tokens per request, a
trivial Python submission resolves in ~0.7 s, and 20 concurrent ones took 7.2 s (§16).

Recommended schedule per grading job, polling all its tokens in one batched GET:

| Attempt | Delay before poll |
|---:|---|
| 1 | 400 ms |
| 2 | 700 ms |
| 3+ | ×1.5 backoff, capped at 3 s |
| — | hard deadline: `wall_time_limit × ntests + 30 s`, then mark `error` |

```
delay = 0.4
deadline = now + wall_time_limit * n_tests + 30
while now < deadline:
    sleep(delay + random.uniform(0, 0.1 * delay))   # jitter
    results = GET /submissions/batch?tokens=<pending, <=20>&fields=...
    persist terminal results; drop those tokens from `pending`
    if not pending: break
    delay = min(delay * 1.5, 3.0)
```

Additional rules:

- **Poll only tokens that are still non-terminal.** Once a slot has `status_id > 2` it is
  immutable; re-fetching it is pure waste.
- **Add jitter.** Without it, 60 candidates who submitted at the same moment poll in lockstep and
  synchronously hammer the 10 Rails threads.
- **Never poll from the candidate's browser directly against Judge0** (§19). The browser polls your
  backend; your backend polls Judge0 once per grading job, and fans the result out.
- **Use `fields=` to trim the response.** `token,status_id,stdout,stderr,compile_output,message,time,memory,exit_code`
  is the minimum set for full result rendering, and `exit_code` is required to classify runtime
  errors (§9).
- **Use Server-Sent Events or WebSocket to push to the candidate**, so 60 browsers polling your
  backend do not become their own load problem.

### 15.8 Test-case authoring safety

- Validate every test case at authoring time by running the reference solution against it through
  Judge0 and asserting status 3. `expected_output` produced by hand is the leading source of
  unfair Wrong Answers.
- Store `stdin`/`expected_output` as **bytes**, and base64 them at the API boundary. Trailing
  newline handling is forgiving (§10) but leading whitespace is not; a text-column round trip that
  trims or re-wraps will silently break test cases.
- **Assert `expected_output` is non-null before submitting a graded run.** A null
  `expected_output` makes every test case pass silently (§10).

---

## 16. Capacity and rate limits for this box

### Measured

A benchmark of 20 concurrent async submissions (trivial Python `a+b`, all Accepted), run against
this deployment:

```
submitted 20 in 0.53s  (38.0 POST/s)
all 20 terminal in 7.20s, polls=17, throughput=2.78 sub/s
statuses: {3: 20}
```

This closely reproduces the previously reported 7.76 s / ~2.6 sub/s. **Use 2.6 submissions/sec as
the planning number** and treat 2.78 as the best case on an otherwise idle box.

Serial `wait=true` round trips on an idle box:

| Language | Latency | Implied ceiling with 2 workers |
|---|---:|---:|
| Python 3.8.1 (71) | 0.22 s | ~9 sub/s theoretical; **2.6–2.8 sub/s observed** under concurrency |
| C++ GCC 9.2.0 (54) | 0.33 s | ~6 sub/s theoretical |
| Java OpenJDK 13 (62) | 1.44 s | **~1.4 sub/s** |

The gap between the theoretical and observed Python numbers is the 2 vCPU host: the two workers,
two Rails processes, Postgres, and Redis all contend for the same two cores.

### What this means for ~60 concurrent candidates

Take a 10-hidden-test-case problem, one submission per test case (§15.2):

| Scenario | Demand | vs 2.6 sub/s |
|---|---:|---|
| 60 candidates, one submit every 5 min, 10 tests | 2.0 sub/s | ~77 % utilised — **workable** |
| 60 candidates, one submit every 3 min, 10 tests | 3.3 sub/s | **127 % — over capacity, backlog grows** |
| 60 candidates, one submit every 2 min, 10 tests | 5.0 sub/s | **192 % — unusable** |
| 60 candidates, Java, every 5 min, 10 tests | 2.0 sub/s vs 1.4 sub/s ceiling | **143 % — over capacity** |
| Simultaneous start: 60 candidates submit at once | 600 submissions | ~**230 s** (3.8 min) to drain |

Conclusions:

- **~60 concurrent candidates is at or slightly past the edge of this box's capacity**, and Java or
  a submit-happy cohort pushes it clearly over. It works only with platform-side queueing and a
  submission cooldown.
- The `MAX_QUEUE_SIZE=500` ceiling is a **~3.2-minute backlog** at 2.6 sub/s. By the time you get a
  503 the platform has been unusable for minutes. 503 is a last resort, not a capacity signal.
- A "simultaneous start" — everyone clicking Run at the same minute — is the real failure mode.

### Required platform-side backpressure

1. **Never let candidate concurrency reach Judge0 directly.** Put a work queue in your backend
   (BullMQ, Sidekiq, Celery, whatever) between the candidate and Judge0, with a **global concurrency
   cap of 2–4 in-flight grading jobs** — matching the 2 workers, not the 500-deep queue.
2. **Read `GET /workers[0].size`** — the live queue depth — before enqueueing. Stop admitting new
   grading jobs above ~100 and surface "judge is busy" to the candidate. Verified working: `size`
   read 8, 8, 6 while ten 1-second submissions drained.
3. **Per-candidate submission cooldown** of 20–30 s and a **per-candidate cap of 1 in-flight
   grading job.** This single rule is worth more than everything else here: it converts an unbounded
   load spike into a bounded one.
4. **Separate "Run sample" from "Submit".** Sample runs use 2–3 test cases; graded submits use all
   N. Encourage sample runs (cheap) and rate-limit submits (expensive) harder.
5. **Never use `wait=true`** — 10 Rails threads total, and it bypasses the queue guard entirely (§5).
6. **Prefer batch** — it is one request per 20 test cases at create and one per poll cycle.
7. Set a **hard deadline per grading job** and mark it `error`/retryable when exceeded, so a wedged
   job does not hold a slot in your own queue forever.

### Operational notes

- **`GET /workers` reports `available`, `idle`, `working`, `paused`, `failed` all as `0`** on this
  deployment, even under load — the Resque worker registration is not visible to the API. Only
  `size` (queue depth) is meaningful. Do not build health checks on `available`.

  ```json
  [{"queue":"1.13.1","size":8,"available":0,"idle":0,"working":0,"paused":0,"failed":0}]
  ```
- **Submissions accumulate forever.** `enable_submission_delete` is `false`, so `DELETE` is 403 and
  there is no API-level purge. The database was 7876 kB at 25 submissions; at 60 candidates × 10
  tests × 20 submits per test session, a single session writes ~12k rows. Monitor disk on the host
  and plan a direct-to-Postgres retention job.
- **913 MiB RAM with 2 GiB swap.** Concurrent submissions at high `memory_limit` will swap and
  wreck latency. Cap `memory_limit` at 256000 KB platform-side (§15.4).
- **No TLS.** The base URL is plain HTTP, so the auth token crosses the network in cleartext. Keep
  the traffic on a private network or a VPN, and restrict port 2358 by security group to your
  backend's IP only.

---

## 17. Ready-to-use client code

Both clients: async submit, batched polling with exponential backoff and jitter, `base64_encoded=true`
throughout, retry on 429/5xx, no retry on 4xx, and a hard deadline. **The token is read from the
environment in both.**

### JavaScript (Node 18+, native `fetch`)

```js
// judge0.mjs
const BASE = process.env.JUDGE0_URL ?? "http://65.0.29.135:2358";
const TOKEN = process.env.JUDGE0_TOKEN;
if (!TOKEN) throw new Error("JUDGE0_TOKEN is not set");

const MAX_BATCH = 20;                 // config_info.max_submission_batch_size
const RESULT_FIELDS =
  "token,status_id,stdout,stderr,compile_output,message,time,memory,exit_code";

const b64 = (s) => (s == null ? null : Buffer.from(s, "utf8").toString("base64"));
const unb64 = (s) => (s == null ? null : Buffer.from(s, "base64").toString("utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class Judge0Error extends Error {
  constructor(status, body) {
    super(`Judge0 HTTP ${status}: ${typeof body === "string" ? body : JSON.stringify(body)}`);
    this.status = status;
    this.body = body;
  }
}

/** HTTP with retry on 429/5xx (incl. 503 "queue is full"); never retries 4xx. */
async function request(path, { method = "GET", body, retries = 5 } = {}) {
  let delay = 500;
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(BASE + path, {
        method,
        headers: {
          "X-Auth-Token": TOKEN,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(30_000),
      });
    } catch (err) {
      if (attempt >= retries) throw err;              // network/timeout -> retry
      await sleep(delay + Math.random() * delay * 0.2);
      delay = Math.min(delay * 2, 8000);
      continue;
    }

    const text = await res.text();
    let parsed = text;
    try { parsed = text ? JSON.parse(text) : null; } catch { /* html 401/403 body */ }

    if (res.ok) return parsed;

    // 401/403/404/422/400 are permanent: fail fast, they are bugs in the request.
    const retryable = res.status === 429 || res.status >= 500;
    if (!retryable || attempt >= retries) throw new Judge0Error(res.status, parsed);

    await sleep(delay + Math.random() * delay * 0.2);
    delay = Math.min(delay * 2, 8000);
  }
}

/** Decode the base64 text fields of one result object, in place. */
function decodeResult(r) {
  if (!r) return r;
  for (const k of ["stdout", "stderr", "compile_output", "message"]) {
    if (k in r) r[k] = unb64(r[k]);
  }
  return r;
}

/**
 * Submit one solution against many test cases and wait for every verdict.
 *
 * @param {{sourceCode: string, languageId: number,
 *          testCases: Array<{stdin?: string, expectedOutput?: string}>,
 *          limits?: object, deadlineMs?: number}} opts
 * @returns {Promise<Array<object>>} results, positionally aligned with testCases
 */
export async function runTestCases({
  sourceCode,
  languageId,
  testCases,
  limits = {},
  deadlineMs = 120_000,
}) {
  const base = {
    language_id: languageId,
    source_code: b64(sourceCode),
    cpu_time_limit: 2.0,
    wall_time_limit: 6.0,
    memory_limit: 256000,
    stack_limit: 128000,
    number_of_runs: 1,
    redirect_stderr_to_stdout: false,
    enable_network: false,          // ALWAYS explicit — never trust the default
    ...limits,
  };

  // 1. create, in chunks of MAX_BATCH
  const tokens = [];
  for (let i = 0; i < testCases.length; i += MAX_BATCH) {
    const chunk = testCases.slice(i, i + MAX_BATCH).map((tc) => ({
      ...base,
      stdin: b64(tc.stdin ?? ""),
      expected_output: tc.expectedOutput == null ? null : b64(tc.expectedOutput),
    }));
    const created = await request("/submissions/batch?base64_encoded=true", {
      method: "POST",
      body: { submissions: chunk },
    });
    for (const c of created) {
      if (!c || !c.token) throw new Judge0Error(422, c); // per-element validation failure
      tokens.push(c.token);
    }
  }

  // 2. poll only the still-pending tokens
  const results = new Map();
  const pending = new Set(tokens);
  const deadline = Date.now() + deadlineMs;
  let delay = 400;

  while (pending.size > 0) {
    if (Date.now() > deadline) {
      throw new Judge0Error(408, `timed out with ${pending.size} submission(s) pending`);
    }
    await sleep(delay + Math.random() * delay * 0.25);   // jitter

    const list = [...pending];
    for (let i = 0; i < list.length; i += MAX_BATCH) {
      const slice = list.slice(i, i + MAX_BATCH);
      const { submissions } = await request(
        `/submissions/batch?tokens=${slice.join(",")}` +
        `&base64_encoded=true&fields=${RESULT_FIELDS}`
      );
      for (const s of submissions) {
        if (!s) continue;                       // unknown token -> null slot
        if (s.status_id > 2) {                  // terminal iff > 2
          results.set(s.token, decodeResult(s));
          pending.delete(s.token);
        }
      }
    }
    delay = Math.min(delay * 1.5, 3000);
  }

  return tokens.map((t) => results.get(t));
}

// --- example ---
// const out = await runTestCases({
//   languageId: 71,
//   sourceCode: "a,b=map(int,input().split())\nprint(a+b)",
//   testCases: [
//     { stdin: "1 2",   expectedOutput: "3"  },
//     { stdin: "10 20", expectedOutput: "30" },
//   ],
// });
// out.forEach((r, i) => console.log(i, r.status_id, JSON.stringify(r.stdout)));
```

### Python (`requests`)

```python
# judge0.py
from __future__ import annotations

import base64
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import requests

BASE = os.environ.get("JUDGE0_URL", "http://65.0.29.135:2358")
TOKEN = os.environ.get("JUDGE0_TOKEN")
if not TOKEN:
    raise RuntimeError("JUDGE0_TOKEN is not set")

MAX_BATCH = 20  # config_info.max_submission_batch_size
RESULT_FIELDS = "token,status_id,stdout,stderr,compile_output,message,time,memory,exit_code"

_session = requests.Session()
_session.headers.update({"X-Auth-Token": TOKEN})


class Judge0Error(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"Judge0 HTTP {status}: {body!r}")
        self.status = status
        self.body = body


def _b64(s: str | None) -> str | None:
    return None if s is None else base64.b64encode(s.encode("utf-8")).decode("ascii")


def _unb64(s: str | None) -> str | None:
    # errors="replace": candidate output is not guaranteed to be valid UTF-8
    return None if s is None else base64.b64decode(s).decode("utf-8", errors="replace")


def _request(path: str, method: str = "GET", body: Any = None, retries: int = 5) -> Any:
    """Retries 429 / 5xx (incl. 503 'queue is full') and network errors. Never retries 4xx."""
    delay = 0.5
    for attempt in range(retries + 1):
        try:
            resp = _session.request(method, BASE + path, json=body, timeout=30)
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(delay + random.uniform(0, delay * 0.2))
            delay = min(delay * 2, 8.0)
            continue

        try:
            parsed = resp.json() if resp.content else None
        except ValueError:
            parsed = resp.text  # 401/403 return text/html

        if resp.ok:
            return parsed

        retryable = resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt >= retries:
            # 400 / 401 / 403 / 404 / 422 are permanent: request bugs, not transients.
            raise Judge0Error(resp.status_code, parsed)

        time.sleep(delay + random.uniform(0, delay * 0.2))
        delay = min(delay * 2, 8.0)

    raise Judge0Error(0, "unreachable")


def _decode(r: dict | None) -> dict | None:
    if r is None:
        return None
    for k in ("stdout", "stderr", "compile_output", "message"):
        if k in r:
            r[k] = _unb64(r[k])
    return r


@dataclass
class TestCase:
    stdin: str = ""
    expected_output: str | None = None


@dataclass
class Limits:
    cpu_time_limit: float = 2.0
    wall_time_limit: float = 6.0
    memory_limit: int = 256000
    stack_limit: int = 128000
    number_of_runs: int = 1
    redirect_stderr_to_stdout: bool = False
    enable_network: bool = False  # ALWAYS explicit — never trust the default

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def run_test_cases(
    source_code: str,
    language_id: int,
    test_cases: Sequence[TestCase],
    limits: Limits | None = None,
    deadline_s: float = 120.0,
) -> list[dict | None]:
    """Submit one solution against N test cases; return results aligned with test_cases."""
    limits = limits or Limits()
    base = {"language_id": language_id, "source_code": _b64(source_code), **limits.as_dict()}

    # 1. create in chunks of MAX_BATCH
    tokens: list[str] = []
    for i in range(0, len(test_cases), MAX_BATCH):
        chunk = [
            {
                **base,
                "stdin": _b64(tc.stdin or ""),
                "expected_output": _b64(tc.expected_output),
            }
            for tc in test_cases[i : i + MAX_BATCH]
        ]
        created = _request(
            "/submissions/batch?base64_encoded=true", "POST", {"submissions": chunk}
        )
        for c in created:
            if not c or "token" not in c:
                raise Judge0Error(422, c)  # per-element validation failure
            tokens.append(c["token"])

    # 2. poll only still-pending tokens
    results: dict[str, dict] = {}
    pending = set(tokens)
    deadline = time.monotonic() + deadline_s
    delay = 0.4

    while pending:
        if time.monotonic() > deadline:
            raise Judge0Error(408, f"timed out with {len(pending)} submission(s) pending")
        time.sleep(delay + random.uniform(0, delay * 0.25))  # jitter

        todo = list(pending)
        for i in range(0, len(todo), MAX_BATCH):
            sl = todo[i : i + MAX_BATCH]
            data = _request(
                f"/submissions/batch?tokens={','.join(sl)}"
                f"&base64_encoded=true&fields={RESULT_FIELDS}"
            )
            for s in data["submissions"]:
                if s is None:            # unknown token -> null slot
                    continue
                if s["status_id"] > 2:   # terminal iff > 2
                    results[s["token"]] = _decode(s)
                    pending.discard(s["token"])
        delay = min(delay * 1.5, 3.0)

    return [results.get(t) for t in tokens]


if __name__ == "__main__":
    out = run_test_cases(
        source_code="a,b=map(int,input().split())\nprint(a+b)",
        language_id=71,
        test_cases=[TestCase("1 2", "3"), TestCase("10 20", "30"), TestCase("-5 5", "1")],
    )
    for i, r in enumerate(out):
        print(i, r["status_id"], repr(r["stdout"]), r["time"], r["memory"])
```

Both clients above were extracted verbatim from this document and executed against
`65.0.29.135:2358` (Node v25.9.0 / Python 3 + `requests`). Each printed per-test verdicts matching
the batch transcript in §7:

```
0 3 "3\n"  0.023 3252
1 3 "30\n" 0.023 3188
2 4 "0\n"  0.018 3260
```

---

## 18. Admin / introspection endpoints

All reachable with the ordinary submission token (§2).

| Endpoint | Purpose |
|---|---|
| `GET /about` | `{"version":"1.13.1", ...}` — cheap liveness check |
| `GET /config_info` | Full effective config (§14) — read this at startup and validate your limits against it |
| `GET /system_info` | Host CPU/memory: `"CPU(s)": "2"`, `"Mem": "913Mi"`, `"Swap": "2.0Gi"` |
| `GET /statistics` | Submission counts by language and status, plus DB size. Cached 10 minutes (`cached_until`). |
| `GET /workers` | `[{"queue":"1.13.1","size":N,...}]` — `size` is live queue depth; the other counters read 0 (§16) |
| `GET /languages` | 47 active languages |
| `GET /languages/all` | 89 including archived |
| `GET /languages/:id` | Adds `source_file`, `compile_cmd`, `run_cmd`, `is_archived` |
| `GET /statuses` | 14 statuses (§9) |
| `GET /license`, `GET /isolate` | 200; licence text |

A useful startup assertion for the platform:

```python
cfg = _request("/config_info")
assert cfg["max_submission_batch_size"] >= 20
assert cfg["enable_batched_submissions"] is True
assert cfg["maintenance_mode"] is False
# fail loudly if the box ever stops allowing what we depend on
```

`GET /statistics` example (real, abridged):

```json
{
  "created_at": "2026-08-05T22:13:21.555+00:00",
  "cached_until": "2026-08-05T22:23:21.555+00:00",
  "submissions": { "total": 25, "today": 25, "last_30_days": { "...": 0 } },
  "languages": [
    { "language": { "id": 71, "name": "Python (3.8.1)" }, "count": 24 },
    { "language": { "id": 54, "name": "C++ (GCC 9.2.0)" }, "count": 1 }
  ],
  "statuses": [
    { "status": { "id": 3, "name": "Accepted" }, "count": 23 },
    { "status": { "id": 4, "name": "Wrong Answer" }, "count": 1 },
    { "status": { "id": 5, "name": "Time Limit Exceeded" }, "count": 1 }
  ],
  "database": { "size_pretty": "7876 kB", "size_in_bytes": 8065507 }
}
```

---

## 19. Security notes

**The auth token is not in this document, and must never be written into source, config committed
to git, a container image, or a client bundle.** It lives in `secret.txt` in this working directory
alongside the Postgres and Redis passwords. Add `secret.txt` and `*.pem` to `.gitignore`, inject
the token as `JUDGE0_TOKEN` from your secret manager at deploy time, and treat any accidental
commit as a full rotation event (`AUTHN_TOKEN` in `judge0.conf`, then restart Judge0).

**Never ship the token to a candidate's browser, and never let a browser call Judge0 directly.**
The platform must proxy every Judge0 call through its own backend.

Concretely, on this deployment a leaked token gives the holder:

- **Full submission capability** — arbitrary code execution on the box, in any of 47 languages,
  including language 44 ("Executable"), which runs an uploaded binary directly.
- **`enable_network: true`** — verified working (§15.5). Outbound internet access from inside your
  VPC, i.e. exfiltration of anything the sandbox can read.
- **`callback_url` to any URL, unvalidated** — an SSRF primitive: Judge0 will `PUT` to any internal
  address the caller names, from inside your network.
- **`GET /workers`, `/statistics`, `/config_info`, `/system_info`** — queue depth, host CPU/RAM,
  submission volume, and the full server configuration. `AUTHZ_TOKEN` being blank does not protect
  these; only the submission token does, and it is the same token.
- Ability to **fill the 500-deep queue** and deny service to every candidate mid-assessment.

Backend proxy requirements:

1. **Build the Judge0 request server-side from an allowlist.** The candidate supplies exactly
   `source_code` and `language_id`. Everything else — limits, `enable_network`, `callback_url`,
   `additional_files`, `compiler_options`, `command_line_arguments`, `number_of_runs`,
   `redirect_stderr_to_stdout` — is set by your backend. Never forward a client JSON body.
2. **Validate `language_id` against your per-problem allowlist** before calling Judge0.
3. **Never return raw Judge0 responses to the browser for hidden test cases.** `stderr` can echo
   the test input; `stdout` is the candidate's program output on secret data. Only `compile_output`
   and `message` are unconditionally safe (§9).
4. **Enforce rate limits in the proxy**, not in Judge0 — Judge0 has none (§16).
5. **The link is plain HTTP.** The token crosses the network in cleartext, as does every
   candidate's source code. Terminate TLS in front of Judge0, or keep the traffic on a private
   network / VPN. Restrict port 2358 by security group to the backend's IP only — `65.0.29.135:2358`
   should not be reachable from the public internet.
6. **Apply an egress firewall rule on the Judge0 host.** Given `allow_enable_network: true`, this
   is the only mitigation that survives a bug in the proxy allowlist.
7. `LightsailDefaultKey-ap-south-1.pem` in this directory is the SSH key for the host. It must never
   be committed and should be `chmod 600`.
