# Preparation-question routing correction

The screenshot exposed a missed route in the previous fix. Removing the test-name
allowlist did not address the verb **prepare**: the information keyword pattern
recognized **preparation**, but not **prepare**. The question
`How should a patient prepare for C-Peptide, Serum?` therefore fell through to the
generic `patient` route and requested demographics. No LabRAG POST was made for that
route. A correction such as `I think you got it wrong` was then parsed as a patient
name because the conversation was waiting for identity details.

The Bone response shown above it is different: it matches the previously verified
API `insufficient_evidence` response for the correctly matched BAP test. The earlier
single direct-query/replay evidence is recorded in
`freeform-labrag-clarification-fix.md`. This fix does not manufacture a specimen or
volume answer when the API abstains, or claim the screenshot alone proves a new
live request occurred.

## Changes

- `app/agents/lab_assistant.py`: general preparation/collection questions recognize
  verb forms and fasting/eating/drinking instructions before patient lookup.
  Information routing clears the identity-waiting flag and sends the original
  laboratory question unchanged. No test names were added to an allowlist.
- Correction phrases are handled as requests to clarify the mistake, not as
  demographic input or a test name to send to the API. They do not automatically
  retry a chargeable query. A later explicit laboratory question routes normally.
- Each HTTP-client information result adds safe request metadata to that assistant
  message's history. `app/ui/streamlit_app.py` displays it in a collapsed **LabRAG
  request details** panel: endpoint path, tool outcome, returned API status/request
  ID and HTTP error code where available. Raw patient context, credentials and
  outbound question contents are not included in this metadata. Earlier response
  details remain available after another turn.
- `app/agents/state.py`: history typing includes structured presentation metadata.
- `tests/test_preparation_routing.py`: verb variants, the exact screenshot sequence,
  recovery from a pending patient lookup, preserved booking/patient routes and UI
  request-detail history. All HTTP calls are mocked.

The appointment package, database records, staff credentials, LabRAG service,
Pinecone index and provider settings are unchanged. No additional live query was
made for this fix.

## Verification and use

The focused knowledge/preparation regression run passed **45 tests**. It verifies
that the screenshot sequence sends the Bone question once and the two explicitly
asked C-Peptide preparation questions once each, with no POST for the intervening
correction. The preparation replies contain the mocked API answer/citations and
never request a patient's name or DOB. A regression starts with an unfinished
patient lookup to verify that a new preparation question recovers correctly.

The full offline suite passed **113 tests in 82.92 seconds**. The local server was
reloaded at `http://127.0.0.1:8501` with the corrected routing.

Refresh the running app, sign in and start a fresh conversation. Ask the preparation
question, then expand its **LabRAG request details** to see the actual API response
status and request ID. Preparation was tested with mocked HTTP, not another live
provider call; whether the current index can answer C-Peptide preparation is not
claimed by these tests. Existing historical messages are not rewritten.
