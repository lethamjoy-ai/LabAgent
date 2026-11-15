from app.agents.planner import ModelPlanner, Plan
from app.agents.lab_assistant import LabAssistant
from app.appointments.gateway import DirectAppointmentGateway
from app.integrations.labrag_client import MockLabRAGClient
from app.appointments.access import AccessContext


class FakeModel:
    def __init__(self, plan):
        self.plan = plan

    def with_structured_output(self, schema):
        assert schema is Plan
        return self

    def invoke(self, messages):
        return self.plan


def test_model_plans_but_cannot_confirm(service):
    model = FakeModel(Plan(intent="booking",fresh_booking=True,patient_id="SYN003",test_id="CBC",
        day="2030-01-15",time="11:00"))
    assistant = LabAssistant(DirectAppointmentGateway(service,service.test_access),MockLabRAGClient(),planner=ModelPlanner(model))
    s = assistant.turn("Schedule CBC on 2030-01-15 at 11:00 with Riley")
    model.plan = Plan(intent="continue")
    s = assistant.turn("Taylor Example, June 10, 1995",s)
    assert "Reply confirm" in s["response"]
    assert len(service.repository.appointments()) == 2
    model.plan = Plan(intent="continue")  # malicious/unhelpful model output
    s = assistant.turn("Could you finalize it",s)
    assert len(service.repository.appointments()) == 2
    model.plan = Plan(intent="confirm")
    s = assistant.turn("confirm",s)
    assert "successfully scheduled" in s["response"]


def test_model_failure_is_bounded_and_clears_consent(service):
    class Broken:
        def normalize(self,*args):
            raise TimeoutError()
    assistant = LabAssistant(DirectAppointmentGateway(service,service.test_access),MockLabRAGClient(),planner=Broken())
    s = assistant.turn("Can you assist me please")
    assert "failed or timed out" in s["response"]
    assert not s.get("confirmation")
