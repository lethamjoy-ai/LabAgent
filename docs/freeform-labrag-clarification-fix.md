# Free-form laboratory test names and clarification-loop fix

## Root cause and request trace

The repeated wording, "Which laboratory test? For example CBC, BUN, Calcium or
C-Peptide", came from **Lab Agent**, not from a LabRAG API response.

Previously, the information route ran through local test-name extraction against
`LAB_TESTS` and required `state.test` before constructing the information tool call.
The Bone Alkaline Phosphatase name did not match that list. `next_request()` returned
the local clarification without calling `HttpLabRAGClient.query()` or POSTing to
the API. The later bare test-name reply still had no match and took the same branch.
The original specimen-and-volume question was not retained for that local branch.

There was a second independent restriction: `lab_question()` rebuilt questions
from a six-test dictionary and a small list of topics/qualifiers. Even if the gate
had been bypassed, that builder could remove the unknown test name and "Bone".
The optional planner also had a test enum and instructions restricting test names.

The HTTP client's request/response validation was not the source of this loop:
its actual request is just `{"question":"..."}` and its response status enum
describes API outcomes, not a permitted list of tests.

## Fix

Knowledge questions now take a dedicated route **before appointment extraction and
validation**. The full original laboratory question goes to the client unchanged,
with `test_id=None` for the legacy method parameter. The runtime HTTP client does
not validate or replace names against any local catalog. No special entry for Bone
Alkaline Phosphatase was added. Other arbitrary names and qualifiers also pass.

The appointment catalog remains CBC/GLU/A1C, with all existing booking/service,
confirmation, authorization, availability and transaction rules unchanged. The
small dictionary retained by the explicit offline mock fixture is not consulted by
runtime knowledge queries. No appointment package files were changed in this fix.

For clarification, state retains the original question and the supplied replies.
A reply such as `Bone Alkaline Phosphatase, Serum` is appended to the full original
request; both "specimen" and "volume" remain present. Fragment replies containing
information keywords, such as `Specimen: Serum`, are also treated as continuation
rather than a new question. A new complete information request starts fresh.

The original answer, status, matched tests and citations remain available. API
`answered` and `insufficient_evidence` responses are presented as returned, with
their appropriate status label and citations. The first genuine API clarification
is asked. If the API repeats the same clarification after a supplied name, the chat
explains that LabRAG still cannot resolve it and suggests an exact catalog test
code, provider, specimen qualifier, or checking the source catalog with staff.
Returned matches and citations are retained; the exact API answer is also available
in the collapsed activity output. A three-response clarification bound handles
changing but unresolved clarification sequences. Repeating an identical supplied
reply does not produce another identical HTTP request. No automatic retries occur.

Patient, staff, DOB and appointment state are never appended to a query. The old
allowlist-based question rewrite was replaced with a conservative privacy guard:
obvious identity-bearing text or known selected patient details cause a local
request to restate the question without those details; **no HTTP call is made**.
Pure laboratory questions are not rewritten. This is a demo guard, not a complete
production PHI detector.

## One live direct query and offline replay

The local `/health` endpoint was available. Exactly one authorized direct POST used:

```json
{"question":"What specimen and volume are required for Bone Alkaline Phosphatase, Serum?"}
```

The API returned:

- Status: **`insufficient_evidence`**.
- Matched test: **`BAP` — `Bone Alkaline Phosphatase, Serum`**.
- Request ID: `4d100eed-b180-42db-9976-e24daa24b3bf`.
- Citations: **0**.
- Explanation: "I cannot answer this from the retrieved passages in the indexed
  partial catalog. This does not establish absence from the full Mayo catalog."

Thus the API recognized the exact test identity, including Bone and Serum, but did
not return source evidence for an answer. This is distinct from the local test-name
loop. It is not proof that the requested facts are absent from all source documents
or that the API itself has a bug.

The direct response was saved and then replayed through the real authenticated
LangGraph + Streamlit script using `httpx.MockTransport` and a disposable synthetic
staff database. The replay made **no additional live request**. It confirmed:
the outgoing question was unchanged, the API status/citations were preserved, and
the returned explanation reached the rendered chat. No specimen or volume values
were invented and no different alkaline-phosphatase test was substituted.

Evidence files (generic lab question/results only, no patient or login data):

- `data/bone-labrag-live-attempt.json`: one-query marker and request.
- `data/bone-labrag-direct-response.json`: actual direct response.
- `data/bone-labrag-replay-report.json`: direct/replay comparison.

`python -m scripts.check_bone_labrag` repeats **only the offline replay**.
Its `--live` flag refuses another request while the attempt marker or response
exists. Do not remove those files just to repeat a chargeable query.

## Files changed and tests

- `app/agents/lab_assistant.py`: early knowledge routing, verbatim requests,
  original-question continuation and bounded clarification handling.
- `app/agents/state.py`: saved query/clarification fields and loop counters.
- `app/agents/planner.py`: free-form information test names and preserved original
  message; booking restrictions remain in the appointment module.
- `app/integrations/lab_question.py`: verbatim questions and an explicit privacy
  guard instead of a name/qualifier allowlist.
- `app/integrations/labrag_client.py`: only the offline mock fixture's test-name
  resolution was adapted; the real HTTP request/response contract is unchanged.
- `app/ui/streamlit_app.py`: exact returned answer in collapsed technical activity.
- `tests/test_freeform_labrag.py`: full exact question, arbitrary names,
  specimen-and-volume continuation, genuine API clarification, insufficient
  evidence, duplicate-query suppression, citations, UI, BUN and booking isolation.
- `tests/test_labrag.py`, `tests/test_staff.py`: existing privacy/call assertions
  updated for verbatim questions rather than local catalog IDs.
- `scripts/check_bone_labrag.py`: guarded direct query plus offline UI replay.
- README and this report: current behavior and evidence.

Run `.venv/Scripts/python.exe -m pytest -q`. Automated tests mock external HTTP and
do not query Groq, OpenAI or Pinecone. No database reset, schema migration, index
rebuild or modification to the separate LabRAG project was performed.

Final offline result: **101 passed in 85.14 seconds**. The local Lab Agent server
was reloaded at `http://127.0.0.1:8501` with the updated routing.

Remaining unverified: whether the current LabRAG index contains sufficient BAP
collection/volume evidence and why it abstained; live answered/clarification cases
for this test (mocked only); and a manual browser session with a real staff login.
The original observed browser/tool state was not persisted, so the pre-fix path was
established by code tracing and offline reproduction, not recovered browser logs.

## Precise follow-up prompt for the separate LabRAG project

> Investigate the existing API's evidence handling for the exact question:
> "What specimen and volume are required for Bone Alkaline Phosphatase, Serum?"
> A single direct POST /api/v1/query returned request ID
> `4d100eed-b180-42db-9976-e24daa24b3bf`, status `insufficient_evidence`,
> matched_tests `[{"test_code":"BAP","test_name":"Bone Alkaline Phosphatase, Serum"}]`,
> and no citations. The explanation said the retrieved passages in the indexed
> partial catalog were insufficient. Lab Agent now forwards this complete question
> unchanged; replaying this response reproduces the same limitation. The former
> local clarification loop has been fixed in Lab Agent.
>
> First inspect local logs, source documents, index/namespace configuration and the
> retrieval/answer/citation-validation code. Trace BAP identity resolution,
> retrieved/reranked passages, specimen-versus-volume coverage, evidence gating
> and citation construction. Determine whether this is correct abstention because
> the indexed subset lacks the facts, or a retrieval/context/evidence/citation
> defect despite present supporting material. Preserve the qualifiers Bone and
> Serum; do not substitute generic alkaline phosphatase or another test.
>
> Use offline fixtures or the saved response under the neighboring LabAgent/data
> directory first. The one live query was already consumed: obtain explicit
> authorization before another chargeable live request. Do not re-ingest documents,
> rebuild or mutate Pinecone, guess missing clinical instructions, or weaken evidence
> validation to force an answer. Report source/page evidence and a narrow fix if a
> verified bug exists, with offline regression tests. If the indexed subset truly
> lacks the evidence, report that limitation accurately and identify the missing
> source coverage rather than inventing an answer. Do not print credentials or
> patient information.
