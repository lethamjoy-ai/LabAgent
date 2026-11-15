> Update: the sidebar now selects OpenAI or Groq. The default is OpenAI, using
> `OPENAI_API_KEY` / `OPENAI_MODEL`; Groq uses `GROQ_API_KEY` / `GROQ_MODEL`.
> Both providers share the existing structured routing and privacy boundaries.
> `LAB_PLANNER` controls the initial selection. No provider fallback occurs.
> OpenAI uses the SDK Responses API with parsed Plan output, `store=False`,
> a 15-second timeout, and zero retries. Switching preserves conversation state
> and does not send a query. Historical Groq-only configuration below is superseded.

Provider-switch verification: **180 offline tests passed**, then **7 OpenAI/provider
tests passed** again after the final wire-schema compatibility change. HTTP and
model responses were mocked; no live OpenAI or Groq query was made. Account access,
API key validity and live model availability remain unverified. Existing keys were
preserved; `.env` now defaults to OpenAI and preserves a separate Groq model setting.
OpenAI uses [parsed structured output](https://developers.openai.com/api/docs/guides/structured-outputs).

# Every-turn LLM routing

Previous Groq mode bypassed the model for exact consent/cancellation, corrections,
patient details and privacy-labelled messages. Default demo mode used keyword
routing. Those production shortcuts are removed. The keyword implementation now
exists only in `tests/mock_router.py`, an explicitly injected offline classifier
double. Production bootstrap requires Groq configuration.

`ModelPlanner` sends a structured Plan request once per authenticated turn.
Plans can contain up to four ordered actions; nested actions, unknown intent/tool
keys, invalid times and invalid dates are rejected before executing the batch.
The same LangGraph action workflow handles business validation and tool results.
Mixed requests execute read-only tasks before booking read-back. Confirmation is
an isolated turn and requires both model classification and exact explicit raw
consent. No automatic provider retries or booking retries were added.

Privacy: patient identity replies are represented by placeholders for classification,
then parsed/matched locally from raw input. Selected and locally stored demographic
values are redacted before the routing call and blocked at the LabRAG question
boundary. Dates in arbitrary utterances are redacted conservatively; raw appointment
dates are parsed locally after routing. Context contains task fields, flags and
sanitized laboratory questions, never patient/appointment records or tokens.
Unlabelled unfamiliar names in arbitrary prose remain a limitation of local
pattern-based privacy filtering; users should enter demographics only in the
requested identity turn. This is not production DLP.

Configuration: `LAB_PLANNER=groq`, `LAB_MODEL` (default
`llama-3.3-70b-versatile`), `GROQ_API_KEY`,
`LAB_ROUTER_MIN_CONFIDENCE=0.7`, `LAB_TIMEZONE`. Existing LabRAG base URL and timeout
remain unchanged. Install `.[dev,model]`. Groq and Grok are different services.
The existing `.env` was not manually inspected, printed or overwritten by this change.

Startup order remains: start LabRAG on port 8000, then
`.venv/Scripts/python.exe -m streamlit run streamlit_app.py`.
Restart the running Streamlit process and sign in again to discard cached assistant
instances after changing code or configuration.

Verification uses mocked structured model output and mocked external HTTP only.
Live routing quality and current provider availability remain unverified; no
chargeable live calls were made. The full offline suite passed **172 tests**.
After the final patient-continuation safeguard, the focused routing, privacy,
planner and booking suite passed **49 tests**, including the additional regression
that keeps the appointment date separate from the patient's DOB.

Changed implementation: `app/agents/planner.py`, `privacy.py`, `lab_assistant.py`,
`state.py`, `app/bootstrap.py`, and `app/integrations/lab_question.py`.
Configuration/docs: `.env.example`, `README.md`, this report.
Tests: `tests/mock_router.py`, `conftest.py`, `test_every_turn_routing.py`,
`test_llm_intent_router.py`, `test_planner.py`, `test_ui.py`, and legacy routing
expectations now referencing the test-only router.
