from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.appointments.repository import Repository
from app.appointments.service import AppointmentService
from app.appointments.gateway import DirectAppointmentGateway
from app.agents.lab_assistant import LabAssistant
from app.integrations.labrag_client import MockLabRAGClient
from app.appointments.access import AccessContext
from app.staff.repository import StaffRepository
from argon2 import PasswordHasher


@pytest.fixture(scope="session")
def staff_password_hash():
    return PasswordHasher().hash("offline-test-password")


@pytest.fixture
def service(tmp_path, staff_password_hash):
    repo = Repository(tmp_path / "appointments.sqlite3")
    zone = ZoneInfo("America/Los_Angeles")
    repo.seed(datetime(2030,1,15).date(), zone)
    staff = StaffRepository(repo)
    for identifier, username, first, role in [("STAFF1","riley.demo","Riley","Staff"),
            ("STAFF2","jordan.demo","Jordan","Staff"),("ADMIN","morgan.demo","Morgan","Admin")]:
        staff.put_account(identifier,username,staff_password_hash,first,"Demo",role,active=True)
        for day in range(7):
            staff.set_hours(identifier,day,"09:00","17:00")
    with repo.connection() as db:
        db.execute("UPDATE appointments SET staff_id='STAFF1'")
    service = AppointmentService(repo, now=lambda: datetime(2030,1,1,tzinfo=zone))
    service.test_access = AccessContext(session_token=service.auth.login("riley.demo","offline-test-password"))
    service.admin_access = AccessContext(session_token=service.auth.login("morgan.demo","offline-test-password"))
    service.other_access = AccessContext(session_token=service.auth.login("jordan.demo","offline-test-password"))
    return service


@pytest.fixture
def assistant(service):
    return LabAssistant(DirectAppointmentGateway(service,service.test_access), MockLabRAGClient(), now=service.now)


@pytest.fixture(autouse=True)
def inject_offline_router(monkeypatch):
    # All chat tests cross classify(); tests needing precise model output inject
    # ModelPlanner with a recording structured-output model instead.
    from tests.mock_router import OfflineRouter
    # Host pauses/clock jumps must not expire offline sessions or confirmation
    # proofs mid-test. Dedicated expiry tests still use an explicit advancing clock.
    monkeypatch.setattr("app.staff.auth.time.time", lambda: 1_700_000_000.0)
    original = LabAssistant.__init__
    def initialize(self,*args,**kwargs):
        if kwargs.get("planner") is None:
            kwargs["planner"] = OfflineRouter()
        original(self,*args,**kwargs)
    monkeypatch.setattr(LabAssistant,"__init__",initialize)
    monkeypatch.setattr("app.agents.planner.groq_planner",lambda *a,**k:OfflineRouter())
    monkeypatch.setenv("LAB_PLANNER","groq")
    monkeypatch.setenv("GROQ_API_KEY","offline-placeholder")
