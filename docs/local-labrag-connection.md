# Local LabRAG API connection

Lab Agent now registers `HttpLabRAGClient` as its real laboratory-information tool.
It calls the existing local service; it does not implement retrieval, embeddings,
Pinecone operations, answer prompts, or provider calls. LabRAG retains ownership of
its pipeline, source/citation validation and credentials. Appointment package code
was not changed for this integration. No additional agent or voice transport was added.

## Startup order and configuration

1. From the neighboring **LabRAG** project root, start its documented API:

   ```powershell
   .venv\Scripts\python.exe -m uvicorn src.api:app --host 127.0.0.1 --port 8000
   ```

2. From the **LabAgent** project root, use the existing command:

   ```powershell
   .venv\Scripts\python.exe -m streamlit run streamlit_app.py
   ```

Set these Lab Agent environment values (also in `.env.example`):

```dotenv
LABRAG_BASE_URL=http://127.0.0.1:8000
LABRAG_TIMEOUT_SECONDS=90
```

The connect timeout is capped at five seconds; the configured timeout applies to
HTTP request phases, including waiting for the answer. It is not a cancellation
signal for work already running in LabRAG. No query retry or redirect following is
configured. A user may explicitly ask again after an error, but the first request
could still have completed on the API side. The old `LABRAG_MODE`, `LABRAG_URL` and
`LABRAG_API_KEY` settings are no longer used by runtime wiring. There is no runtime
mock selector or fallback. Explicit synthetic fixtures remain only for offline
tests. Lab Agent's optional planning-provider settings are unchanged and independent
of the LabRAG service's own providers/credentials.

## Verified API contract

Inspected the running `GET /openapi.json` and `GET /health` before implementation.
Health returned `{"status":"ok","external_connectivity":"not_checked"}`. This is
a liveness result, not a promise that every provider/index is reachable.

`POST /api/v1/query` accepts only `{"question":"..."}` (1–3000 characters).
The response requires `status`, `answer`, `citations`, `matched_tests`, and
`request_id`. Status is `answered`, `needs_clarification` or `insufficient_evidence`.
`matched_tests` entries contain nullable `test_code` and `test_name`.

Each citation preserves these nullable string fields from OpenAPI:
`source_id`, `document_id`, `document_title`, `provider`, `test_code`, `test_name`,
`printed_catalog_page`, `pdf_page_1_based`, `original_pdf_page_1_based`, `section`,
`chunk_id`, `source_url`, `citation_role`. Unknown additional citation fields are
retained too. Printed pages are never relabeled as PDF pages or converted into
another numbering system. Text displays identify each page kind explicitly.

The chat presents the returned answer, matched tests and readable citations. The
Activity expander contains technical response status/request ID and full citation
metadata; database/appointment records are never put into that output. An answered
response without citations receives an explicit missing-source warning.

## Clarification, privacy and failure behavior

The main graph evaluates the API result:

- `answered`: show the returned answer and citations; clear clarification state.
- `needs_clarification`: ask the returned question and retain the original local
  laboratory question. Subsequent replies form a new complete question with the
  original topic/test and accumulated laboratory qualifiers. There is one POST per
  explicit user turn, with no automatic follow-up calls.
- `insufficient_evidence`: label the evidence limitation and present the API's
  explanation without fabricating specimen/collection instructions.

The privacy boundary reconstructs a laboratory-only question from the existing
test catalog, recognized information topics and laboratory qualifiers. It does not
forward raw conversation/history, selected patient, DOB, appointments or staff
identity. Raw original/clarification utterances remain local. This conservative
demo vocabulary covers CBC, glucose, A1c, BUN, Calcium and C-Peptide, with collection,
preparation, stability, methodology and related recognized topics. Unknown free
text is not transmitted verbatim. Supporting broader nuanced free-form qualifiers
requires extending this privacy-safe question adapter; it does not require adding
another retrieval pipeline or reasoning agent.

Failures have distinct structured tool codes and public messages:
connection failure, client timeout, other transport failure, malformed JSON/schema,
HTTP 422 request rejection, HTTP 503 service unavailability, HTTP 504 upstream
timeout, and other HTTP errors. Error bodies are not echoed into chat. None of these
states switches to a mock answer. An empty `answered` response is malformed.

## Verification

Automated tests use `httpx.MockTransport` and temporary synthetic staff databases.
They cover all three statuses, complete citation preservation, printed/PDF/original
page labels, clarification across multiple turns, exclusion of patient names/DOB,
connection/timeout failures, HTTP status handling, invalid JSON/schema, missing
citations and UI rendering. The appointment, authentication, authorization and
concurrency regressions run alongside them. No automated test calls live providers.

Exactly **one authorized live BUN query** was sent through the actual authenticated
LangGraph + Streamlit AppTest workflow, using a disposable database and an enforced
one-query limit. No real patient/staff database was used by the check.

- Question sent: `For laboratory test BUN (Blood urea nitrogen): Please provide specimen requirements.`
- API status: **answered**.
- Request ID: `74160f7a-59ab-467d-ad5e-2f8412fda50f`.
- Matched test: `BUN`, `Blood Urea Nitrogen (BUN), Serum`.
- Citations: **3**, all from the returned Mayo catalog references.
- Answer reached rendered chat: **yes**.
- Every citation reached rendered chat: **yes**.
- Page values preserved: printed `501`, PDF `2.0`, original PDF `502.0`.

`scripts/check_labrag_ui.py --live-query` is an explicit manual check, excluded from
pytest. Each invocation authorizes/sends up to one new live query; do not rerun it
merely to repeat the existing verification. The script prints a structured report
without exposing its disposable random staff password.

Final offline suite result: **89 passed in 70.17 seconds**, including existing
appointment, authorization, session and concurrent-booking tests.

Remaining unverified behavior: live clarification/error scenarios were mocked, not
charged live; no bulk queries or browser session using a real staff account were
run. UI verification used Streamlit's actual script and rendered element tree in
AppTest, not a manual browser screenshot. Health alone does not validate external
dependencies. Test execution results are recorded in the final implementation reply.

Changed files: `app/integrations/labrag_client.py`, new `lab_question.py`,
`app/bootstrap.py`, `app/agents/{lab_assistant,state}.py`,
`app/ui/streamlit_app.py`, `.env.example`, `tests/{test_labrag,test_ui}.py`,
new `scripts/check_labrag_ui.py`, README and this new report. Existing appointment
module files and historical reports were not modified.
