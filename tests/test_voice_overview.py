from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.appointments.voice_demo import setup, PATIENT, STAFF

SECRET="offline-overview-secret-"*3


@pytest.fixture
def context(tmp_path):
    # UTC is already Jan 2, but the lab is still on Jan 1.
    voice=setup(tmp_path/"demo.sqlite3",SECRET,
        now=lambda:datetime(2030,1,2,1,tzinfo=ZoneInfo("UTC")))
    api=create_app(labrag=Mock(),secret=SECRET,voice_appointments=voice)
    return voice,TestClient(api),{"X-LabAssist-Webhook-Secret":SECRET}


def test_local_today_tomorrow_and_readonly(context):
    voice,client,headers=context
    path=Path(voice.service.repository.path)
    before=path.read_bytes()
    url="/api/v1/tools/get_daily_overview"
    today=client.post(url,headers=headers,json={"day":"today"}).json()
    tomorrow=client.post(url,headers=headers,json={"day":"tomorrow"}).json()
    assert today["status"]=="empty" and today["date"]=="2030-01-01" and today["appointments"]==[]
    assert tomorrow["date"]=="2030-01-02" and tomorrow["count"]==1
    slot=tomorrow["appointments"][0]
    assert slot["booking_reference"]=="DEMO-VOICE-001" and slot["time"]=="09:00"
    assert slot["timezone"]=="America/Los_Angeles" and slot["utc_offset"]=="-0800"
    assert "patient" not in str(tomorrow) and "birth" not in str(tomorrow)
    assert path.read_bytes()==before


def test_no_other_identity_or_staff_records(context):
    voice,client,headers=context
    with voice.service.repository.connection() as db:
        db.execute("INSERT INTO patients(id,name) VALUES('OTHER','Other Synthetic')")
        db.execute("INSERT INTO staff VALUES('OTHER-STAFF','other-internal','!disabled','Other','Demo','Staff',1)")
        for ref,patient,staff in [("PRIVATE-ONE","OTHER",STAFF),("PRIVATE-TWO",PATIENT,"OTHER-STAFF")]:
            db.execute("INSERT INTO appointments(id,patient_id,test_id,start,status,request_id,staff_id) VALUES(?,?,?,?,?,?,?)",
                       (ref,patient,"CBC","2030-01-02T10:00:00-08:00","scheduled",ref,staff))
    result=client.post("/api/v1/tools/get_daily_overview",headers=headers,json={"day":"tomorrow"})
    assert result.json()["count"]==1 and "PRIVATE" not in result.text
    voice.service.auth.logout(voice.access.session_token)
    assert client.post("/api/v1/tools/get_daily_overview",headers=headers,json={"day":"tomorrow"}).status_code==503


@pytest.mark.parametrize("body",[{}, {"day":"yesterday"},{"day":3},{"day":"today","staff_id":STAFF},{"day":"tomorrow","organization":True}])
def test_validation_and_authentication(context,body):
    voice,client,headers=context
    url="/api/v1/tools/get_daily_overview"
    before=Path(voice.service.repository.path).read_bytes()
    assert client.post(url,json=body).status_code==401
    assert client.post(url,headers={"X-LabAssist-Webhook-Secret":"wrong"},json=body).status_code==401
    assert client.post(url,headers=headers,json=body).status_code==422
    assert client.post(url,headers=headers,content=b"x"*17000).status_code==413
    assert Path(voice.service.repository.path).read_bytes()==before


def test_live_agent_accepts_overview_webhook():
    from app.integrations.live_voice import require_readonly_agent,LiveSetupError
    client=Mock()
    tools=[{"name":name,"type":"webhook","api_schema":{"method":"POST","url":"https://example.test/api/v1/tools/"+name}}
           for name in ("lookup_lab_test","get_daily_overview")]
    client.conversational_ai.agents.get.return_value.model_dump.return_value={
        "conversation_config":{"agent":{"prompt":{"tools":tools}}},"platform_settings":{"auth":{"enable_auth":True}}}
    require_readonly_agent(client,"demo")
    tools[-1]["api_schema"]["method"]="GET"
    with pytest.raises(LiveSetupError):
        require_readonly_agent(client,"demo")
