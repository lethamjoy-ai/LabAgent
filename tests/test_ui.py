from streamlit.testing.v1 import AppTest


def ui_for(service,monkeypatch):
    from app.integrations.labrag_client import MockLabRAGClient
    monkeypatch.setattr("app.bootstrap.build_labrag_client",lambda:MockLabRAGClient())
    monkeypatch.setenv("LAB_DATABASE",service.repository.path)
    monkeypatch.setenv("LAB_PLANNER","groq")
    return AppTest.from_file("../streamlit_app.py").run(timeout=30)


def sign_in(ui,username="riley.demo",password="offline-test-password"):
    ui.text_input[0].set_value(username)
    ui.text_input[1].set_value(password)
    next(b for b in ui.button if b.label == "Sign in").click().run(timeout=30)
    return ui


def test_text_interface_booking(service,monkeypatch):
    ui = ui_for(service,monkeypatch)
    assert not ui.exception and not ui.chat_input
    sign_in(ui)
    assert ui.chat_input
    ui.chat_input[0].set_value("Schedule CBC 2099-01-15 at 11:00 with Jordan").run(timeout=30)
    assert "patient's first name" in ui.session_state["conversation"]["response"]
    ui.chat_input[0].set_value("Taylor Example, June 10, 1995").run(timeout=30)
    assert not ui.exception
    assert "Reply confirm" in ui.session_state["conversation"]["response"]
    assert "Jordan Demo" in ui.session_state["conversation"]["response"]
    ui.chat_input[0].set_value("confirm").run(timeout=30)
    assert not ui.exception
    assert "successfully scheduled" in ui.session_state["conversation"]["response"]


def test_staff_overview_login_logout_and_history(service,monkeypatch):
    with service.repository.connection() as db:
        db.execute("UPDATE patients SET date_of_birth=NULL,address_line1='PRIVATE ADDRESS',postal_code='00001' WHERE id='SYN001'")
    ui = ui_for(service,monkeypatch)
    sign_in(ui,password="incorrect")
    assert ui.error and not ui.chat_input
    sign_in(ui)
    ui.chat_input[0].set_value("Show my schedule 2030-01-15").run(timeout=30)
    assert not ui.exception
    table = ui.dataframe[0].value
    assert list(table.columns) == ["Patient Name","Date of Birth","Laboratory Test","Appointment Time","Status"]
    assert table.iloc[0]["Date of Birth"] == "Not available"
    assert "PRIVATE ADDRESS" not in table.to_string() and "SYN001" not in table.to_string()
    assert not ui.session_state["conversation"]["overview_slots"]
    assert not any("Available Appointment Slots" in m.value for m in ui.markdown)
    ui.chat_input[0].set_value("specimen CBC").run(timeout=30)
    assert len(ui.dataframe) == 1
    old_assistant = ui.session_state["assistant"]
    old_state = ui.session_state["conversation"]
    next(b for b in ui.button if b.label == "Log out").click().run(timeout=30)
    assert not ui.dataframe and not ui.chat_input
    assert old_assistant.turn("confirm",old_state)["history"] == []
    sign_in(ui,"jordan.demo")
    assert not ui.dataframe and "conversation" not in ui.session_state
    ui.chat_input[0].set_value("Show my schedule 2030-01-15").run(timeout=30)
    assert "0 appointments" in ui.session_state["conversation"]["response"]
    assert "Alex Demo" not in ui.session_state["conversation"]["response"]


def test_expired_ui_session_hides_history(service,monkeypatch):
    ui = sign_in(ui_for(service,monkeypatch))
    ui.chat_input[0].set_value("Show my schedule 2030-01-15").run(timeout=30)
    with service.repository.connection() as db:
        db.execute("UPDATE staff_sessions SET expires=0")
    ui.run(timeout=30)
    assert not ui.chat_input and not ui.dataframe
    assert "conversation" not in ui.session_state
    assert ui.warning
