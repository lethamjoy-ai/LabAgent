# Prepared LabRAG replies

LabAssist prepares the complete authorized answer, source links, and any table
before emitting the new assistant chat message. One request spinner covers the
workflow and preparation. It does not stream partial answer text or rerun the
workflow to display the result.

The answer stays visible. A directly following `View sources` expander starts
collapsed. Source link labels show the printed catalog page and section. Each
citation remains a separate entry, including distinct sections in the same PDF.
Only HTTP(S) URLs are clickable; unsupported or missing links get an explicit
unavailable label. Returned URLs are preserved without guessing which PDF page
number they target.

Full citation dictionaries, including extra fields and separate printed,
PDF, and original-PDF page numbers, remain in conversation state and sidebar
diagnostics. The original complete textual response is retained for non-UI
consumers. The UI uses a separate answer field so source metadata is not printed
inline. No query or retrieval behavior was changed.

`LabRAG request details` now sits inside the same collapsed sidebar section as
Intent routing and Activity. Expanding sources or diagnostics is frontend-only.
A server rerun also renders stored records without resubmitting the request.

## Verification and timing

* 47 focused offline tests passed across LabRAG HTTP/status handling, source
  rendering, schedules, login, and booking continuation.
* Six final source-rendering and performance tests passed after adding explicit
  assertions for one loading indicator, source preparation inside that indicator,
  and answer emission after preparation.
* A mocked BUN answered request took 35.16 ms; preparation took 0.04 ms; server
  enqueue from the answer text to the complete message took 1.14 ms. There were
  two distinct source sections in the same PDF and exactly one HTTP call.
* The same run's populated schedule request took 32.50 ms; table preparation took
  10.04 ms; summary-to-complete enqueue took 1.23 ms. A repeat request took
  27.93 ms, preparation 0.72 ms, and summary-to-complete enqueue 0.84 ms.

These are individual Streamlit AppTest samples with synthetic SQLite fixtures,
mocked routing, and mocked LabRAG HTTP. `request_ms` measures request handling
through completion of the workflow. `response_preparation_ms` is separate.
`first_content_to_complete_enqueue_ms` measures server enqueue after the initial
text element; it is **not** browser-visible paint time. Diagnostics label the
measurements as server timings. The backend never receives a browser paint
timestamp, so it cannot report actual visible-content delay.

The browser connector reported `The browser access host is unavailable` during
this change. Thus live-browser simultaneous schedule/table display, collapsed BUN
sources, and first-visible-to-complete paint timing remain unverified. No live
BUN or other chargeable query was issued. The verified behavior is through the
same Streamlit entry point with mocked HTTP, including a rerun proving no second
request. Refresh and sign in at http://127.0.0.1:8502 for browser acceptance.

Changed: `app/ui/streamlit_app.py`, `app/ui/sources.py`,
`app/agents/lab_assistant.py`, `tests/test_labrag.py`,
`tests/test_labrag_rendering.py`, and this report.
