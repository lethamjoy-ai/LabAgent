import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import pytest
from argon2 import PasswordHasher
from app.appointments.access import AccessContext
from app.appointments.gateway import DirectAppointmentGateway
from app.appointments.models import GatewayRequest
from app.agents.lab_assistant import LabAssistant
from app.integrations.labrag_client import MockLabRAGClient
from app.staff.auth import AuthService,token_hash
from tests.test_appointments import details


def test_passwords_login_expiration_and_logout(service):
    auth = AuthService(service.repository,ttl_seconds=20,now=lambda:100)
    row = auth.staff.by_username("riley.demo")
    assert row["password_hash"].startswith("$argon2id$")
    assert "offline-test-password" not in row["password_hash"]
    assert PasswordHasher().verify(row["password_hash"],"offline-test-password")
    assert auth.login("riley.demo","wrong") is None
    assert auth.login("missing","offline-test-password") is None
    token = auth.login(" RILEY.DEMO ","offline-test-password")
    assert auth.current(token).id == "STAFF1"
    assert "password_hash" not in auth.current(token).model_dump()
    with service.repository.connection() as db:
        assert db.execute("SELECT token_hash FROM staff_sessions WHERE token_hash=?",(token_hash(token),)).fetchone()
        assert not db.execute("SELECT 1 FROM staff_sessions WHERE token_hash=?",(token,)).fetchone()
    auth.now = lambda:121
    assert auth.current(token) is None
    auth.now = lambda:100
    token = auth.login("riley.demo","offline-test-password")
    auth.logout(token)
    assert auth.current(token) is None
    with service.repository.connection() as db:
        db.execute("UPDATE staff SET is_active=0 WHERE id='STAFF2'")
    assert auth.current(service.other_access.session_token) is None
    assert auth.login("jordan.demo","offline-test-password") is None


def test_two_staff_can_share_slot_but_not_one_staff(service):
    one = details()
    two = one.model_copy(update={"staff_id":"STAFF2"})
    a = service.prepare(one,"one",service.test_access)
    b = service.prepare(two,"two",service.test_access)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda args:service.book(*args,access=service.test_access),
            [(one,"one",a.confirmation_token),(two,"two",b.confirmation_token)]))
    assert all(r.ok for r in results)
    assert {r.booking.staff_id for r in results} == {"STAFF1","STAFF2"}
    assert not service.prepare(one,"three",service.test_access).ok
    with service.repository.connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO appointments VALUES(?,?,?,?,?,?,?)",("duplicate","SYN001","CBC",one.start.isoformat(),"scheduled","dup","STAFF1"))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO appointments VALUES(?,?,?,?,?,?,?)",("invalid-fk","SYN001","CBC",one.start.isoformat(),"scheduled","bad-fk","NONEXISTENT"))


def test_own_schedule_and_admin_boundary(service):
    other = details(12).model_copy(update={"staff_id":"STAFF2"})
    proof = service.prepare(other,"assign-other",service.test_access)
    assert service.book(other,"assign-other",proof.confirmation_token,service.test_access).ok
    one = DirectAppointmentGateway(service,service.test_access)
    two = DirectAppointmentGateway(service,service.other_access)
    admin = DirectAppointmentGateway(service,service.admin_access)
    req = GatewayRequest(action="overview",day=date(2030,1,15))
    assert len(one.execute(req).display_rows) == 2
    assert len(two.execute(req).display_rows) == 1
    assert one.execute(req.model_copy(update={"target_staff_name":"Jordan"})).code == "access_denied"
    assert one.execute(req.model_copy(update={"action":"admin_overview"})).code == "access_denied"
    assert len(admin.execute(req.model_copy(update={"action":"admin_overview"})).display_rows) == 3
    assert not one.execute(GatewayRequest(action="lookup",appointment_id=service.repository.appointments()[-1].id)).ok
    assert not one.execute(GatewayRequest(action="patient_appointments",patient_id="SYN003")).display_rows


def test_working_hours_inactive_and_unassigned_holds(service):
    day = date(2030,1,15)
    service.staff.set_hours("STAFF2",day.weekday(),"12:00","14:00")
    slots = service.availability(day,"CBC","STAFF2")
    assert [s.start.strftime("%H:%M") for s in slots] == ["12:00","12:30","13:00","13:30"]
    with service.repository.connection() as db:
        db.execute("DELETE FROM staff_hours WHERE staff_id='STAFF2'")
    assert not service.availability(day,"CBC","STAFF2")
    with service.repository.connection() as db:
        db.execute("UPDATE appointments SET staff_id=NULL WHERE start LIKE '%09:00:00%'")
    assert not [s for s in service.availability(day,"CBC") if s.start.hour == 9 and s.start.minute == 0]
    assert len(service.overview(day,access=service.admin_access,organization=True).display_rows) == 2


def test_working_hours_can_be_reconfigured_without_inventing_shifts(service):
    day = date(2030,1,15)
    service.staff.replace_hours("STAFF2",[day.weekday()],"12:00","14:00")
    assert len(service.availability(day,"CBC","STAFF2")) == 4
    assert not service.availability(date(2030,1,16),"CBC","STAFF2")
    with pytest.raises(ValueError):
        service.staff.replace_hours("STAFF2",[9],"09:00","17:00")
    assert len(service.availability(day,"CBC","STAFF2")) == 4


def test_local_setup_has_no_default_password_and_is_idempotent(service):
    from scripts.setup_staff import provision
    config = [{"id":"NEW-DEMO","username":"new.demo","first_name":"New","last_name":"Demo","role":"Staff",
               "weekdays":[0,1,2,3,4],"start":"09:00","end":"17:00"}]
    assert provision(service.auth,config) == ["new.demo"]
    row = service.staff.by_username("new.demo")
    assert row["is_active"] == 0 and row["password_hash"].startswith("$argon2id$")
    assert service.auth.login("new.demo","password") is None
    assert provision(service.auth,config) == []
    assert service.staff.by_username("new.demo")["password_hash"] == row["password_hash"]
    service.staff.activate_password(row["id"],PasswordHasher().hash("operator-entered-password"))
    token = service.auth.login("new.demo","operator-entered-password")
    assert service.auth.current(token).username == "new.demo"
    service.staff.activate_password(row["id"],PasswordHasher().hash("another-operator-password"))
    assert service.auth.current(token) is None


def test_confirmation_cannot_cross_staff_or_session(service):
    d = details()
    proof = service.prepare(d,"protected",service.test_access)
    for access in [service.other_access,AccessContext(session_token=service.auth.login("riley.demo","offline-test-password"))]:
        with pytest.raises(ValueError,match="Confirmation"):
            service.book(d,"protected",proof.confirmation_token,access)
    altered = d.model_copy(update={"staff_id":"STAFF2"})
    with pytest.raises(ValueError,match="Confirmation"):
        service.book(altered,"protected",proof.confirmation_token,service.test_access)
    assert service.book(d,"protected",proof.confirmation_token,service.test_access).ok
    service.auth.logout(service.test_access.session_token)
    with pytest.raises(PermissionError):
        service.book(d,"protected",proof.confirmation_token,service.test_access)


def test_staff_conversation_assignment_and_denied_colleague_schedule(service):
    a = LabAssistant(DirectAppointmentGateway(service,service.test_access),MockLabRAGClient(),now=service.now)
    s = a.turn("Schedule CBC 2030-01-15 at 11:00 with Jordan")
    assert "patient's first name" in s["response"]
    s = a.turn("Alex Demo, January 15, 1990",s)
    assert s["pending"]["staff_id"] == "STAFF2"
    assert "Jordan Demo" in s["response"]
    s = a.turn("Yes",s)
    assert "with Jordan Demo" in s["response"]
    s = a.turn("Show Jordan's schedule today",s)
    assert "only your own" in s["response"] and not s["overview_rows"]
    s = a.turn("How many patients am I seeing 2030-01-15?",s)
    assert "2 appointments" in s["response"]
    s = a.turn("Schedule CBC for Taylor with Jordan tomorrow at 1 PM",s)
    assert "patient's first name" in s["response"] and s["patient"] is None
    assert s["target_staff_name"] == "Jordan"


def test_unavailable_colleague_offers_named_alternative(service):
    d = details().model_copy(update={"staff_id":"STAFF2"})
    proof = service.prepare(d,"taken",service.test_access)
    service.book(d,"taken",proof.confirmation_token,service.test_access)
    a = LabAssistant(DirectAppointmentGateway(service,service.test_access),MockLabRAGClient(),now=service.now)
    s = a.turn("Schedule CBC 2030-01-15 11:00 with Jordan")
    s = a.turn("Alex Demo, January 15, 1990",s)
    assert "Riley Demo" in s["response"] and "unavailable" in s["response"]
    assert not s.get("confirmation")
    s = a.turn("11:00 with Riley",s)
    assert "Riley Demo" in s["response"] and s["confirmation"]


def test_session_isolation_and_shared_labrag(service):
    class Shared:
        def __init__(self): self.calls = []
        def query(self,test_id,question):
            self.calls.append((test_id,question))
            return MockLabRAGClient().query(test_id,question)
    client = Shared()
    a = LabAssistant(DirectAppointmentGateway(service,service.test_access),client,now=service.now)
    b = LabAssistant(DirectAppointmentGateway(service,service.other_access),client,now=service.now)
    old = a.turn("Show my schedule 2030-01-15")
    switched = b.turn("What specimen is required for BUN?",old)
    assert "Alex Demo" not in str(switched["history"])
    for question,test in [("What specimen is required for BUN?","BUN"),
            ("What is the minimum volume for Calcium?","CALCIUM"),
            ("What preparation is required for C-Peptide?","C-PEPTIDE")]:
        first,second = a.turn(question),b.turn(question)
        assert first["response"] == second["response"]
        assert "SYNTHETIC MOCK" in first["response"]
        assert test.lower() in client.calls[-1][1].lower()
    service.auth.logout(service.test_access.session_token)
    blocked = a.turn("specimen BUN",old)
    assert blocked["history"] == [] and not blocked["tool_results"]
