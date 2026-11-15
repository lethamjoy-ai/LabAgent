# Booking continuation and availability diagnostics

## Investigation

The supplied sequence does not establish that tomorrow has availability:
`2030-01-15` is a Tuesday, while "tomorrow" is resolved anew at the booking turn.
Read-only inspection at the time of this fix found the lab configured for
`America/Los_Angeles`, with local date November 7, 2026. Tomorrow was therefore
**Sunday, November 8, 2026**. All three active staff accounts had working hours for
Monday through Friday only, and none had a Sunday shift. The earlier Tuesday
search could correctly show slots while Sunday's booking could not.

Code inspection and an offline reproduction verified that a new `Schedule` request
already cleared the prior search date/staff. It resolved tomorrow, parsed 4 PM as
16:00, and preserved CBC/date/time across the patient-details turn. The DOB was
handled as identity input and did not replace the appointment date. No additional
DOB parsing/storage change was made, and no patient data was repaired or reset.

The actual defects were the loss of diagnostic detail when the gateway received
an empty slot list, the unconditional "That staff member" wording with no selected
staff member, the empty "Available times: No available slots" suffix, and the lack
of a resolved date in patient-detail clarification. Previously, all empty searches
became the same generic unavailable response.

The browser's original in-memory conversation/tool calls are not persisted as an
audit trail and could not be independently inspected. The exact original event
timestamp is unknown. The state/tool arguments are verified through offline
reproduction with fixed clocks, and the currently stored shifts were inspected
directly without modifying SQLite. No live external APIs were called.

## Changes

Appointment rules remain in `app/appointments/`; no second agent was added.

- `AppointmentService.prepare_request()` owns staff matching, exact-slot checks
  and assignment proposals, replacing scheduling choices in the direct gateway.
  Explicit staff preference is honored. Without one, the response names all
  staff available at the requested time and explicitly offers an assignee before
  requesting confirmation; the earlier search's staff is not silently reused.
- `availability_result()` returns structured `availability_reason` values:
  `missing_configuration`, `no_shifts`, `no_open_slots`, or `available`.
  An unavailable requested time/assignee with actual alternatives uses
  `selected_slot_unavailable`. Missing weekly configuration is distinguished from
  a configured employee with no shift that day and from configured but exhausted
  future slots. Empty results are not inferred merely from appointment counts.
- Failure messages show the full requested date/time and timezone. An unnamed
  staff preference never becomes "that staff member". Actual alternatives include
  staff names and times; without alternatives, the response offers to check another
  date (and requests configuration maintenance where appropriate).
- Clarifications include the resolved full appointment date, ISO date, test, time
  and lab timezone. Clock injection is normalized to the lab timezone before
  deriving today/tomorrow, including when a supplied clock is in UTC.
- The identity turn continues the pending booking. The service readback includes
  patient, full test name, full date, local time, timezone and the offered/selected
  staff member. Changing the staff offer obtains a new confirmation token.
- The existing exact-details/session-bound confirmation, availability recheck,
  transaction, uniqueness and idempotency safeguards remain in effect. No booking
  success is returned before persistence commits.

Files changed: `app/appointments/{models,service,gateway}.py`,
`app/agents/lab_assistant.py`, and new
`tests/test_booking_continuation.py`. This report is new; historical reports and
LabRAG/Pinecone were not modified. There is no database migration for this fix.

## Verification

Focused cases cover the exact dated-search/new-tomorrow sequence, later patient
details, UTC/lab-date boundaries, no staff preference, explicit staff offers,
missing configuration, a day without shifts, a full configured day, unavailable
staff/time combinations with real alternatives, and staff changes requiring a new
readback. Booking tests verify no insertion before confirmation and no duplicate
on a confirmed retry. All data written by tests resides in temporary fixtures.

Run `.venv/Scripts/python.exe -m pytest -q` for the complete offline suite.
Final result: **71 passed in 55.45 seconds**. The focused continuation module
passed all **9 tests**. The local app was reloaded and its health check returned
`ok` at `http://127.0.0.1:8501`.
Existing patient, appointment, confirmation, staff and working-hours records were
fingerprinted through read-only connections to check preservation. Setting staff
hours or booking an actual appointment against the real database was deliberately
not attempted. Production voice transport and live external integrations are not
verified by these offline checks.

The before/after fingerprint matched:
`b06b544092996c3bfe3546c4c3e728f7a8a0a94139d48bafee0290ee4e180f3c`.
