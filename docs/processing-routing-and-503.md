# Processing/storage routing and C-Peptide HTTP 503

The screenshot shows two independent failures, rather than two laboratory answers.

## Processing/storage question

`How should CPR be processed and stored?` was not recognized by the router's
enumerated information words and verbs. It returned generic capability help without
calling LabRAG. Earlier fixes added preparation wording but left this fall-through.

The router now checks explicit booking, staff schedule, availability and patient
record intents first, then sends otherwise unclaimed natural reference questions
to LabRAG. Processing, storage, refrigeration, freezing and centrifugation questions
no longer require their verbs or test names to be added to a list. Explicit patient
record requests still go to patient lookup, while generic patient instructions go
to laboratory information. General assistance/finalization turns retain their
existing handling. The exact abbreviation **CPR** is preserved, not corrected to a
different test; LabRAG may ask for clarification if it cannot identify it.

## Confirmed C-Peptide service failure

The screenshot's HTTP 503 indicates that an HTTP response came back, but LabRAG
could not complete the query. A healthy `/health` response does not validate its
providers or retrieval dependencies: that endpoint explicitly returns
`external_connectivity: not_checked`.

After the user explicitly authorized **one live C-Peptide request**, this exact
question was sent once, with no automatic retry:

`How should a patient prepare for C-Peptide, Serum?`

Result:

- HTTP status: **503**.
- API error category: **`service_unavailable`**.
- Request ID: **`8a480360-1608-465b-9bf9-f27bd5571466`**.
- No laboratory answer or citations were returned.

Evidence: `data/cpeptide-live-attempt.json` and `data/cpeptide-live-result.json`.
`scripts/check_cpeptide_once.py` refuses a duplicate attempt while its marker exists.
No live CPR/CRP query was sent.

Read-only inspection of the neighboring `LabRAG/src/api.py` shows that it converts
provider rate limits to `provider_rate_limited`/503, identifiable timeouts to 504,
and other caught pipeline exceptions to `service_unavailable`/503. The reported
category is generic; it does not establish a particular credential, model, Pinecone,
embedding or retrieval failure. `LocalRAGService` initializes those dependencies
lazily inside the query operation. The API's catch block neither returns the
underlying exception nor logs it, and no matching persisted server log was found
in the inspected log locations. The API process was running on port 8000.

Consequently the underlying **LabRAG service fault remains unresolved**. Changing
Lab Agent routing or fabricating preparation instructions cannot repair it. No
LabRAG credentials were read, no sibling-project code was edited, no index was
modified, and no provider error body was exposed.

## Client diagnostics and privacy

Lab Agent now preserves the local API's known error category and a validated UUID
request ID from the error body or `X-Request-ID` header. These appear in the collapsed
per-response request details and latest Activity output, enabling server correlation.
Only these safe fields are accepted. Arbitrary error strings, provider responses,
credentials, echoed questions and patient details are not included.

If the API explicitly reports `provider_rate_limited`, the message explains that
condition. Otherwise HTTP 503 says the query could not complete and **no laboratory
answer was returned**, rather than presenting the failure as a specimen/preparation
answer. Query requests are still never automatically retried or replaced by mock
answers. A generic 503 is not relabeled as a rate limit or timeout without evidence.

Changed files: `app/agents/lab_assistant.py`, `app/integrations/labrag_client.py`,
`app/ui/streamlit_app.py`, new `tests/test_storage_and_service_errors.py`, new
`scripts/check_cpeptide_once.py`, and this report. Appointment module code, database
records, provider configuration and prior reports were not changed.

Offline tests exercise the exact screenshot sequence with mocked HTTP: the first
question is POSTed unchanged, and the second reports a real-shaped service error
with its request ID. They also test arbitrary processing/storage phrasing, preserved
booking and record routes, safe error metadata, header fallback, unknown/private
error-body suppression, UI request details and no retries.

Final offline verification: **135 passed in 83.48 seconds**. The app was reloaded
at `http://127.0.0.1:8501` and its health check returned `ok`. No further LabRAG
query was made during reload.

## Precise next step in the LabRAG project

Use this prompt in the separate LabRAG workspace:

> Investigate HTTP 503 `service_unavailable` for request
> `8a480360-1608-465b-9bf9-f27bd5571466`, question:
> "How should a patient prepare for C-Peptide, Serum?".
> Lab Agent sent one direct authorized query to `POST /api/v1/query` and captured
> this response. `/health` is healthy but reports external connectivity unchecked.
> The API catch block currently masks the exception without a diagnostic log.
>
> Inspect the local API/service initialization and pipeline failure handling. Add
> sanitized server diagnostics that identify request ID, execution stage, exception
> class and known error category without printing credentials, question text,
> patient data or unrestricted provider bodies. Distinguish configuration,
> embedding/Pinecone connection, retrieval and LLM/provider failures. Fix a verified
> cause rather than assuming a rate limit, missing key or model error.
>
> Use mocked/offline failures first. The authorized live request has already been
> consumed; obtain permission before another chargeable query. Do not re-ingest
> documents, rebuild/mutate Pinecone, fabricate collection instructions or weaken
> evidence validation. Add offline API error-mapping/diagnostic tests and report
> the actual root cause plus any remaining uncertainty.
