from typing import TypedDict, Any


class AssistantState(TypedDict, total=False):
    history: list[dict[str, Any]]
    message: str
    response: str
    intent: str
    patient: str | None
    test: str | None
    requested_date: str | None
    available_slots: list[dict]
    selected_slot: str | None
    confirmation: str | None
    confirmed: bool
    request_id: str | None
    pending: dict | None
    tool_results: list[dict[str, Any]]
    tool_request: dict | None
    iterations: int
    patient_name: str | None
    date_of_birth: str | None
    appointment_filter: str
    lookup_id: str | None
    awaiting_identity: bool
    overview_rows: list[dict]
    overview_slots: list[str]
    context_session: str
    target_staff_name: str | None
    appointment_rows: list[dict]
    availability_slots: list[str]
    labrag_original_question: str | None
    labrag_clarifications: list[str]
    labrag_last_question: str | None
    labrag_last_query: str | None
    labrag_clarification_count: int
    labrag_last_clarification: str | None
    routing: dict[str, Any]

    routed_plan: dict[str, Any]
    queued_plans: list[dict[str, Any]]
    queued_action_message: str | None
    action_id: str | None
    matched_action_id: str | None
    confirmation_action_id: str | None
    first_name: str | None
    last_name: str | None
    pending_action_message: str | None
    requested_time: str | None
    awaiting_booking_fields: list[str]
    booking_field_reply: bool
