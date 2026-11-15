from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo
from app.appointments.models import BookingDetails, GatewayRequest
from app.appointments.gateway import DirectAppointmentGateway
from app.appointments.access import AccessContext


def details(hour=11, patient="SYN003", test="CBC"):
    return BookingDetails(patient_id=patient, test_id=test,
        start=datetime(2030,1,15,hour,tzinfo=ZoneInfo("America/Los_Angeles")),staff_id="STAFF1")


def test_seed_overview(service):
    overview = service.overview(date(2030,1,15),access=service.test_access)
    assert len(overview.appointments) == 2
    assert [a.status for a in overview.appointments] == ["scheduled","completed"]
    assert not overview.slots
    slots = service.availability(date(2030,1,15),"CBC","STAFF1")
    assert len(slots) == 14
    assert 9 not in [s.start.hour for s in slots if s.start.minute == 0]


def test_confirmation_and_idempotency(service):
    d = details()
    assert not service.book(d,"retry",None,service.test_access).ok
    proof = service.prepare(d,"retry",service.test_access)
    first = service.book(d,"retry",proof.confirmation_token,service.test_access)
    second = service.book(d,"retry",proof.confirmation_token,service.test_access)
    assert first.ok and first.booking.id == second.booking.id
    assert len(service.repository.appointments()) == 3
    gateway = DirectAppointmentGateway(service,service.test_access)
    wrong = gateway.execute(GatewayRequest(action="book", details=details(12), request_id="retry", confirmation_token=proof.confirmation_token))
    assert not wrong.ok


def test_changed_details_and_expiry(service):
    d = details()
    proof = service.prepare(d,"change",service.test_access)
    gateway = DirectAppointmentGateway(service,service.test_access)
    for changed in [details(12),details(patient="SYN001"),details(test="A1C")]:
        result = gateway.execute(GatewayRequest(action="book",details=changed,request_id="change",confirmation_token=proof.confirmation_token))
        assert not result.ok
    with service.repository.connection() as db:
        db.execute("UPDATE confirmations SET expires=0")
    assert not gateway.execute(GatewayRequest(action="book",details=d,request_id="change",confirmation_token=proof.confirmation_token)).ok


def test_concurrent_bookings(service):
    d = details()
    one = service.prepare(d,"race-1",service.test_access)
    two = service.prepare(d,"race-2",service.test_access)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda pair: service.book(d,*pair,access=service.test_access),
            [("race-1",one.confirmation_token),("race-2",two.confirmation_token)]))
    assert sum(r.ok for r in results) == 1
    loser = next(r for r in results if not r.ok)
    assert loser.code == "unavailable"
    assert d.start not in [s.start for s in loser.slots if s.staff_id == d.staff_id]
    assert len(service.repository.appointments()) == 3


def test_concurrent_retries(service):
    d = details()
    proof = service.prepare(d,"same-request",service.test_access)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: service.book(d,"same-request",proof.confirmation_token,service.test_access),range(2)))
    assert all(r.ok for r in results)
    assert results[0].booking.id == results[1].booking.id


def test_validation(service):
    gateway = DirectAppointmentGateway(service,service.test_access)
    for d in [details(8),details(patient="UNKNOWN"),details(test="TSH"),
              details().model_copy(update={"start":datetime(2030,1,15,11)})]:
        assert not gateway.execute(GatewayRequest(action="prepare",details=d,request_id="bad")).ok
