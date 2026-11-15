# Login and schedule rendering performance

## Evidence and scope

Measurements use the real Streamlit entry point through `AppTest`, synthetic
fixtures in isolated SQLite files, and a mocked intent router. They are server /
test-harness elapsed times, not browser paint or live-provider latency. No live
LLM or LabRAG query was made. The running demo SQLite file was opened read-only;
a simple appointment count completed in 1.68 ms. That does not exclude lock
contention at other times or measure the real user's earlier session.

The browser connector reported that its host was unavailable. A native browser
fallback timed out waiting for app access. Thus browser click-to-paint timing,
websocket delivery, and the currently authenticated browser session could not
be inspected. No claim of a browser-verified fix is made.

## Causes and changes

* Successful login previously called `st.rerun()` before constructing the
  workspace. The form can remain on screen while that rerun initializes the
  assistant. Login now authenticates once, empties its placeholder immediately,
  shows a loading spinner during initialization, and emits the workspace in the
  same script run. Invalid credentials keep the form and existing error.
* Every `AuthService` construction previously generated another expensive
  Argon2 dummy hash. The process now reuses one random dummy hash, which has no
  associated account. Real credentials, credential checks, authorization,
  session expiry, and logout are not cached or bypassed.
* Chat previously finished the workflow and reran the entire page before
  emitting the new response. The current result is now rendered directly and
  saved for future history renders, without replaying routing or SQL.
* The first populated dataframe incurred cold pandas/Arrow renderer imports.
  These modules now warm in a background worker while the user fills in the
  login form. The worker touches no credentials, patient data, Streamlit state,
  database, or model. If it has not finished, rendering waits for normal imports.
  Its cost is overlapped, not eliminated; very fast submissions can still wait.
* Each authorized response prepares its own Arrow table before emitting its
  summary. Both use the same returned message and retain the existing five
  columns. No extra lookup or model call is needed. Empty schedules explicitly
  say that no assigned appointments match.
* One collapsed sidebar `Intent routing` section holds the latest routing,
  numeric timings, and `Activity · latest request`. Per-message intent expanders
  and Demo configuration were removed. Opening the expander is frontend-only;
  even a server rerun only renders the stored trace.

## Before and after

Same three requests and isolated-fixture setup in fresh pytest processes:

| Scenario | Before (ms) | After (ms) |
| --- | ---: | ---: |
| Sign-in action through usable AppTest workspace | 288.80 | 173.39 |
| First populated schedule, explicit fixture date | 1270.38 | 66.78 |
| Repeat identical populated schedule | 65.91 | 44.88 |
| Empty `Show my schedule today` | 62.28 | 56.13 |

These are individual diagnostic samples, not a statistical benchmark or latency
SLA. The first implementation that removed reruns but did not warm the renderer
still took 1239 ms for the first populated table, isolating the cold-renderer
cost from rerun savings.

After-change login breakdown: credential verification 117.31 ms;
authentication-to-workspace enqueue 40.67 ms, including assistant initialization
32.08 ms. Stage instrumentation was not present in the before run, so an exact
before-stage breakdown is unavailable. The old path had one explicit post-auth
rerun; the new path has none, asserted by the UI regression test.

First populated schedule breakdown: workflow 35.34 ms; mocked intent routing
about 1 ms; authorized tool/database execution 11.27 ms; response formatting
0.16 ms (including table-row preparation 0.07 ms); UI Arrow preparation 6.42 ms;
summary/table enqueue 1.59 ms. Submit-to-result-ready was 37.44 ms. Stage values
overlap (formatting includes row preparation) and need not sum to total time.
The remaining workflow time includes authorization, local privacy processing,
and graph/state overhead. Live model latency remains unmeasured and is not
removed or bypassed by this change.

`database_lookup_ms` additionally measures the authorized schedule gateway call,
including access checks, SQL, and result mapping; it is not SQL-engine-only
time. Diagnostics distinguish this from all-tool execution, routing, formatting,
UI preparation, and render enqueue. Enqueue timings exclude browser paint.
No raw prompts, names, dates of birth, passwords, tokens, or SQL parameters are
added to timing diagnostics or shared caches.

## Verification

`tests/test_ui_performance.py` checks single-click successful sign-in, failed
sign-in, logout, populated and empty `Show my schedule today`, the exact five
columns, one routing call and tool result per submission, same-message summary
and table, numeric-only timing values, sidebar diagnostics, no Demo configuration,
and no work replay on a rerun. Existing booking and confirmation tests remain
part of the full offline suite. No real appointment was created.

Results: full offline suite **238 passed**; final focused UI, continuation, and
booking checks **22 passed** after the final rendering assertions. LabAssist was
restarted on port 8502 and its health endpoint returned `ok`. The demo database
fingerprint was identical across the final read-only checks and restart. All
test writes were confined to isolated fixture databases; no live sign-in or
booking was performed against the demo database.

Run focused checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_performance.py tests/test_ui.py tests/test_booking_field_continuation.py -q
```

Refresh the running app after restart and sign in again. Browser verification
still needed: click-to-workspace paint, submit-to-summary paint, summary-to-table
paint, and first/repeat live-router latency. The sidebar now provides the server
breakdown needed to compare those browser observations.
