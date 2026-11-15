from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.appointments.voice_demo import setup, CreateRequest, PATIENT, STAFF

SECRET = "offline-synthetic-secret-" * 3


@pytest.fixture
def voice(tmp_path):
    return setup(tmp_path / "voice.sqlite3",SECRET,now=lambda:datetime(2030,1,1,tzinfo=ZoneInfo("America/Los_Angeles")))


def offer(voice, time="13:00"):
    return voice.create(CreateRequest(action="offer",test_id="CBC",date="2030-01-02",time=time))


def confirm(voice, token):
    return voice.create(CreateRequest(action="confirm",confirmed=True,confirmation_token=token))


def count(voice):
    with voice.service.repository.connection() as db:
        return db.execute("SELECT count(*) FROM appointments").fetchone()[0]


def test_lookup_readonly_and_scoped(voice):
    with voice.service.repository.connection() as db:
        db.execute("INSERT INTO patients(id,name) VALUES('OTHER','Other Synthetic')")
        db.execute("INSERT INTO appointments(id,patient_id,test_id,start,status,request_id,staff_id) VALUES(?,?,?,?,?,?,?)",
                   ("OTHER-REF","OTHER","CBC","2030-01-03T09:00:00-08:00","scheduled","other",STAFF))
    path=voice.service.repository.path
    from pathlib import Path
    before=Path(path).read_bytes()
    assert voice.lookup("DEMO-VOICE-001")["appointment"]["timezone"]=="America/Los_Angeles"
    assert voice.lookup("unknown")["status"]=="not_found"
    assert voice.lookup("OTHER-REF")["status"]=="not_found"
    assert Path(path).read_bytes()==before


def test_offer_confirm_retry_atomic_result(voice):
    initial=count(voice)
    result=offer(voice)
    assert result["status"]=="confirmation_required" and count(voice)==initial
    token=result["confirmation_token"]
    first=confirm(voice,token)
    assert first["status"]=="confirmed"
    assert first==confirm(voice,token)
    with voice.service.repository.connection() as db:
        db.execute("UPDATE confirmations SET expires=0 WHERE token=?",(token,))
    assert first==confirm(voice,token)  # Successful retries survive offer expiry.
    assert count(voice)==initial+1
    with voice.service.repository.connection() as db:
        row=db.execute("SELECT * FROM appointments WHERE id=?",(first["appointment"]["booking_reference"],)).fetchone()
    assert row["patient_id"]==PATIENT and row["staff_id"]==STAFF
    assert row["start"].startswith("2030-01-02T13:00")
    assert voice.lookup(first["appointment"]["booking_reference"])["appointment"]==first["appointment"]


def test_expired_invalid_taken(voice):
    assert confirm(voice,"x"*32)["status"]=="invalid_confirmation"
    token=offer(voice)["confirmation_token"]
    with voice.service.repository.connection() as db:
        db.execute("UPDATE confirmations SET expires=0 WHERE token=?",(token,))
    assert confirm(voice,token)["status"]=="expired_confirmation"
    a=offer(voice)["confirmation_token"]
    b=offer(voice)["confirmation_token"]
    assert confirm(voice,a)["status"]=="confirmed"
    unavailable=confirm(voice,b)
    assert unavailable["status"]=="unavailable" and unavailable["alternatives"]
    assert all(s["time"]!="13:00" for s in unavailable["alternatives"])


def test_concurrent_same_and_competing_offers(voice):
    initial=count(voice)
    token=offer(voice)["confirmation_token"]
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:confirm(voice,token),range(4)))
    assert all(r==results[0] for r in results) and count(voice)==initial+1
    tokens=[offer(voice,"14:00")["confirmation_token"] for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda t:confirm(voice,t),tokens))
    assert sorted(r["status"] for r in results)==["confirmed","unavailable"]
    assert count(voice)==initial+2


def test_auth_validation_and_no_unconfirmed_write(voice):
    client=TestClient(create_app(labrag=Mock(),secret=SECRET,voice_appointments=voice))
    headers={"X-LabAssist-Webhook-Secret":SECRET}
    initial=count(voice)
    for tool in ("get_appointment_details","create_appointment"):
        url="/api/v1/tools/"+tool
        assert client.post(url,json={}).status_code==401
        assert client.post(url,headers={"X-LabAssist-Webhook-Secret":"wrong"},json={}).status_code==401
        assert client.post(url,headers=headers,json={}).status_code==422
    url="/api/v1/tools/create_appointment"
    body={"action":"offer","test_id":"CBC","date":"2030-01-02","time":"13:00"}
    offered=client.post(url,headers=headers,json=body).json()
    for patch in ({"confirmed":False},{"date":"2030-01-03"},{"patient_id":"other"}):
        request={"action":"confirm","confirmation_token":offered["confirmation_token"],"confirmed":True,**patch}
        assert client.post(url,headers=headers,json=request).status_code==422
    confirmation={"action":"confirm","confirmation_token":offered["confirmation_token"],"confirmed":"true"}
    assert client.post(url,headers=headers,json=confirmation).status_code==422
    assert count(voice)==initial
    assert client.post(url,headers=headers,json={**body,"date":"2030-02-31"}).status_code==422
    found=client.post("/api/v1/tools/get_appointment_details",headers=headers,json={"booking_reference":"DEMO-VOICE-001"})
    assert found.json()["status"]=="found"
    confirmation["confirmed"]=True
    result=client.post(url,headers=headers,json=confirmation)
    assert result.json()["status"]=="confirmed" and count(voice)==initial+1
    assert client.post(url,headers=headers,json=confirmation).json()==result.json()
    assert count(voice)==initial+1


def test_missing_auth_configuration_and_missing_storage(voice, monkeypatch, tmp_path):
    client=TestClient(create_app(labrag=Mock(),secret="",voice_appointments=voice))
    assert client.post("/api/v1/tools/create_appointment",json={}).status_code==503
    monkeypatch.setenv("LAB_DATABASE",str(tmp_path/"not-provisioned.sqlite3"))
    client=TestClient(create_app(labrag=Mock(),secret=SECRET))
    assert client.post("/api/v1/tools/get_appointment_details",headers={"X-LabAssist-Webhook-Secret":SECRET},
                       json={"booking_reference":"unknown"}).status_code==503
    assert not (tmp_path/"not-provisioned.sqlite3").exists()


def test_missing_working_hours_and_no_open_slots(voice):
    with voice.service.repository.connection() as db:
        db.execute("DELETE FROM staff_hours WHERE staff_id=?",(STAFF,))
    result=offer(voice)
    assert result["status"]=="unavailable" and result["reason"]=="missing_configuration"
    assert result["alternatives"]==[]


def test_live_agent_allows_only_approved_tools():
    from app.integrations.live_voice import require_readonly_agent, LiveSetupError
    client=Mock()
    tools=[{"name":name,"type":"webhook","api_schema":{"method":"POST","url":"https://example.test/api/v1/tools/"+name}}
           for name in ("lookup_lab_test","get_appointment_details","create_appointment")]
    config={"conversation_config":{"agent":{"prompt":{"tools":tools}}},"platform_settings":{"auth":{"enable_auth":True}}}
    client.conversational_ai.agents.get.return_value.model_dump.return_value=config
    require_readonly_agent(client,"synthetic")
    tools[-1]["api_schema"]["url"]="https://example.test/unrelated"
    with pytest.raises(LiveSetupError):
        require_readonly_agent(client,"synthetic")


def test_provisioned_storage_reopens_without_mutation(voice,monkeypatch):
    from pathlib import Path
    from app.appointments.voice_demo import configured
    path=Path(voice.service.repository.path)
    monkeypatch.setenv("LAB_DATABASE",str(path))
    before=path.read_bytes()
    reopened=configured(SECRET)
    assert reopened.lookup("DEMO-VOICE-001")["status"]=="found"
    assert path.read_bytes()==before
    setup(path,SECRET)  # Explicit setup is safe to repeat.
    assert count(voice)==1
    with pytest.raises(PermissionError):
        configured("different-secret"*4).lookup("DEMO-VOICE-001")


def test_setup_preserves_existing_workspace_records(tmp_path,monkeypatch):
    from app.appointments.repository import Repository
    from app.appointments.voice_demo import database_path
    path=tmp_path/"workspace.sqlite3"
    repo=Repository(path)
    with repo.connection() as db:
        db.execute("INSERT INTO patients(id,name) VALUES('EXISTING','Existing Synthetic')")
        db.execute("INSERT INTO appointments(id,patient_id,test_id,start,status,request_id) VALUES(?,?,?,?,?,?)",
                   ("ORIGINAL","EXISTING","CBC","2030-01-02T09:00:00-08:00","scheduled","original"))
        before=dict(db.execute("SELECT * FROM appointments WHERE id='ORIGINAL'").fetchone())
    monkeypatch.setenv("LAB_DATABASE",str(path))
    monkeypatch.setenv("LABASSIST_VOICE_DATABASE",str(tmp_path/"ignored.sqlite3"))
    assert database_path()==path.resolve()
    voice=setup(path,SECRET,now=lambda:datetime(2030,1,1,tzinfo=ZoneInfo("America/Los_Angeles")))
    assert voice.lookup("ORIGINAL")["status"]=="not_found"
    assert voice.lookup("DEMO-VOICE-001")["appointment"]["time"]=="09:30"
    setup(path,SECRET)
    with repo.connection() as db:
        assert dict(db.execute("SELECT * FROM appointments WHERE id='ORIGINAL'").fetchone())==before
        assert db.execute("SELECT count(*) FROM appointments").fetchone()[0]==2


def test_reserved_identity_collision_rolls_back(tmp_path):
    from app.appointments.repository import Repository
    path=tmp_path/"workspace.sqlite3"
    repo=Repository(path)
    with repo.connection() as db:
        db.execute("INSERT INTO patients(id,name) VALUES(?,?)",(PATIENT,"Existing Synthetic"))
    with pytest.raises(ValueError,match="Reserved"):
        setup(path,SECRET)
    with repo.connection() as db:
        assert not db.execute("SELECT 1 FROM staff WHERE id=?",(STAFF,)).fetchone()
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='voice_demo_config'").fetchone()
