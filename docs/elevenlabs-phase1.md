# ElevenLabs voice demo, Phase 1

The existing text workflow remains available. Voice is off for every new
Streamlit session and after logout. Enable it in the sidebar when needed.

## Configure and launch

Install project dependencies in the existing virtual environment:

```powershell
uv pip install --python .venv\Scripts\python.exe -e ".[model]"
```

Set these server-only settings in `.env` (placeholders also live in
`.env.example`; do not put real keys into source control):

```dotenv
ELEVENLABS_API_KEY=<your existing server key>
ELEVENLABS_VOICE_ID=<a voice ID available to your ElevenLabs account>
ELEVENLABS_STT_MODEL=scribe_v2
ELEVENLABS_TTS_MODEL=eleven_multilingual_v2
```

The existing LabRAG URL, router provider/key, timezone, staff authentication,
and database settings are still used. No ElevenLabs agent, new retrieval
pipeline, or patient voice workflow is created.

1. Start the existing LabRAG API on port 8000 using its existing startup command.
2. Start LabAssist from this project root:

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8502
```

Sign in with an existing synthetic demo staff account. Enable voice, then:

1. Read the disclosure: audio is sent to ElevenLabs on **Transcribe recording**.
   Use synthetic examples only; never record patient names, DOBs, or records.
2. Explicitly press the native mic start control. The control shows recording
   activity. Stop it within 30 seconds, or use **Cancel recording / transcript**
   to discard the recording and reset the mic.
3. Choose **Transcribe recording**. The transcript appears in an editable field.
   Nothing is submitted to the router or LabRAG yet.
4. Review/edit the question and choose **Send reviewed question**.
5. Read the complete answer and collapsed **View sources** in chat. A separate
   audio job generates a short, extractive spoken answer. Press Play when ready;
   the same audio can be replayed without another provider request.

If mic permission is denied, use the ordinary text input; it stays available.
Missing settings, unreadable/empty audio, empty transcripts, provider failures,
and unavailable playback produce brief fallback messages rather than raw errors.

## Boundaries and request lifecycle

`VoiceSession` uses the existing `LabAssistant` workflow with independent state.
It shares the configured router and LabRAG client, not the text conversation,
selected patient, booking registry, proof, or confirmation. Its routing wrapper
allows only information questions, information clarification, or a continuation
of its own pending laboratory clarification. Explicit appointment/record
commands are rejected even if a router incorrectly calls them information.
Mixed action batches are rejected. Its appointment gateway denies every execute
call, so there is no voice path to booking, schedules, lookup or confirmation.
The existing local identity privacy guard also runs before LabRAG access.

An edited transcript is still treated as voice input and cannot escape this
restriction. A spoken `confirm` directs the user to text without touching a
pending text booking. Text confirmation continues through its original exact
proof and authorization checks. Staff sessions are rechecked before STT, Send,
and audio playback.

Recording hashes are scoped to a recorder attempt, and one-use draft IDs are
session-local. Failed recordings are not silently retried; **Retry transcription**
explicitly retries the current failed recording. Reruns never retranscribe,
resubmit Send, or regenerate audio. After Send finishes, only the mic/draft epoch
advances and a fresh **Record next question** widget appears. Cancel also advances
the epoch. The key stays stable during recording, transcription and editing.
A genuinely new attempt may contain identical audio or repeat the same question;
an old attempt or submitted draft cannot be replayed as a new Send. A recorder
widget error offers **Start a new recording** to remount it. Playback uses the session's
completed future. Disabling voice makes no further STT/query calls; completed
answers/audio remain readable/replayable until logout or New conversation.

Raw audio is not written to disk or the database by application code. Uploads,
transcripts, and generated audio are held only in session memory, not in shared
application caches or timing logs. SDK exceptions are replaced with fixed error
messages. ElevenLabs service-side retention follows the account's controls;
local log suppression is not a guarantee of provider zero retention. Its
`enable_logging=false` mode requires enterprise access, so this demo does not
silently enable an account-dependent mode. Synthetic-only use is mandatory.

STT accepts WAV recordings of 0.1–30 seconds and at most 2 MB. SDK requests use
the existing 30-second timeout and `max_retries=0`. TTS uses MP3 and at most
600 characters of cleaned answer text, retaining complete sentences. If an
answer cannot safely fit, playback directs the listener to the full answer.
URLs, numbered citation markers, and diagnostic appendices are removed. The
text/citations are retained in full. Insufficient evidence and missing-source
warnings are spoken rather than presented as verified instructions.

TTS runs in a session-owned background executor, with a polling UI fragment;
answer rendering never waits for it. Audio failures leave the answer visible.
Queued tasks are canceled on logout/reset. An in-flight provider call may finish
after logout, but the revoked session cannot render its result.

## Official contract references

The implementation and mocked installed-SDK HTTP tests follow the official
[speech-to-text quickstart](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/speech-to-text),
[transcription API](https://elevenlabs.io/docs/api-reference/speech-to-text/convert),
[speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), and
[Streamlit audio-input API](https://docs.streamlit.io/develop/api-reference/widgets/st.audio_input).

## Verification

New tests cover SDK request shapes/no retries, bounded/empty recordings, empty
transcripts, sanitized provider errors, faithful excerpts, editable review,
explicit Send, source preservation, session replay/deduplication, missing config,
mic failure fallback, voice-off defaults, and logout. Scope tests deliberately
misclassify spoken bookings/confirmations and assert no appointment gateway
calls or row changes, then prove that the pending text confirmation still works.
All writes in tests use isolated synthetic SQLite fixtures.

Final offline result: **274 passed**. LabAssist was restarted on port 8502 and
its health endpoint returned `ok`. The read-only demo database fingerprint was
identical across the restart. No real appointment was created or edited.

Browser smoke test still required: record **What specimen is required for BUN?**,
edit/review it, Send, check the full answer and collapsed sources, then Play.
Real mic permission/activity/cancel behavior, speech recognition accuracy,
account voice access, playback, and perceived text-before-audio timing require
that browser test. Do not create an appointment while testing voice.

At implementation time, the browser connector reported its host unavailable,
and `ELEVENLABS_VOICE_ID` was not configured. The onboarding check validated the
existing key and SDK setup. No live STT, TTS, or LabRAG query was sent for this
phase. Supply the voice ID and refresh/restart before the browser smoke test.

Changed implementation files: `app/integrations/elevenlabs_voice.py`,
`app/agents/voice_session.py`, `app/ui/voice_panel.py`,
`app/ui/streamlit_app.py`, `app/voice/adapter.py`, `.env.example`, and `README.md`.
New regression files: `tests/test_elevenlabs_voice.py`,
`tests/test_voice_session.py`, and `tests/test_voice_ui.py`. Three older UI tests
(`test_freeform_labrag.py`, `test_preparation_routing.py`,
`test_storage_and_service_errors.py`) were aligned with the already-implemented
collapsed sources/sidebar diagnostics; their HTTP and status assertions remain.

## Recorder lifecycle follow-up

The first implementation advanced the mic key only on Cancel, leaving the
submitted upload/error mounted after Send. Whole-session audio-hash deduplication
also treated a deliberately new identical recording as a duplicate. The fix
rotates mic/form/transcript keys after every completed Send (including insufficient
evidence or audio failure), and scopes hash deduplication to that attempt. Only
the current draft is reset; history, citations and audio futures are preserved.

`tests/test_voice_recorder_lifecycle.py` covers two back-to-back questions in one
Streamlit AppTest session, identical repeated recordings/questions, edited drafts,
stable keys during review, fresh keys after Send, first-audio replay after the
second turn, transcription retry/discard, stale widget recovery, insufficient
evidence, and simulated TTS failure. Providers and native recorder uploads are
mocked. Browser access again reported its host unavailable, so real microphone
record/stop/error recovery remains unverified. No live provider call is needed
for these offline checks.

Existing pre-fix Streamlit session objects are upgraded in place on rerun,
preserving their history and audio. A pending edited transcript is retained;
an already-submitted old recording is remounted with a fresh mic. This avoids
restarting the server and discarding current chats just to apply this fix.
