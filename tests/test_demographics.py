import sqlite3
from datetime import date
from app.appointments.repository import Repository, DEMOGRAPHIC_FIELDS


def test_migration_backs_up_and_preserves_every_record(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE patients(id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE appointments(id TEXT PRIMARY KEY, patient_id TEXT, test_id TEXT, start TEXT UNIQUE, status TEXT, request_id TEXT UNIQUE);
        CREATE TABLE confirmations(token TEXT PRIMARY KEY,details TEXT,request_id TEXT UNIQUE,expires REAL);
        INSERT INTO patients VALUES('keep-id','Original Person');
        INSERT INTO appointments VALUES('booking','keep-id','CBC','2030-01-15T11:00:00-08:00','scheduled','keep-request');
        INSERT INTO confirmations VALUES('keep-token','original-details','keep-confirmation',123);
        """)
        before = {t: db.execute(f"SELECT * FROM {t}").fetchall() for t in ("patients","appointments","confirmations")}
    repo = Repository(path)
    assert set(repo.migration["added"]) == set(DEMOGRAPHIC_FIELDS)
    with sqlite3.connect(repo.migration["backup"]) as backup:
        for table, records in before.items():
            assert backup.execute(f"SELECT * FROM {table}").fetchall() == records
    with repo.connection() as db:
        assert tuple(db.execute("SELECT id,name FROM patients").fetchone()) == before["patients"][0]
        assert all(db.execute(f"SELECT {f} FROM patients").fetchone()[0] is None for f in DEMOGRAPHIC_FIELDS)
        for table, columns in [("appointments","id,patient_id,test_id,start,status,request_id"),("confirmations","token,details,request_id,expires")]:
            assert [tuple(r) for r in db.execute(f"SELECT {columns} FROM {table}")] == before[table]
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT staff_id FROM appointments").fetchone()[0] is None
        assert db.execute("SELECT actor_staff_id,session_hash FROM confirmations").fetchone()[:] == (None,None)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    again = Repository(path)
    assert again.migration == {"added":[],"backup":None}
    assert again.staff_migration == {"changed":False,"backup":None}
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_demographics_storage_and_seed_preservation(service):
    values = ("1990-01-15","Nonbinary","1 Synthetic Way","Demo City","CA","US","00123-AB")
    repo = service.repository
    with repo.connection() as db:
        db.execute("UPDATE patients SET date_of_birth=?,gender=?,address_line1=?,city=?,state=?,country=?,postal_code=? WHERE id='SYN001'",values)
    repo.seed(date(2030,1,15),service.timezone)
    p = repo.patient("SYN001")
    assert p.date_of_birth == date(1990,1,15)
    assert tuple(getattr(p,f) for f in DEMOGRAPHIC_FIELDS[1:]) == values[1:]
    assert p.age_on(date(2030,1,14)) == 39
    assert p.age_on(date(2030,1,15)) == 40
    with repo.connection() as db:
        columns = {r["name"]:r for r in db.execute("PRAGMA table_info(patients)")}
        assert "age" not in columns
        for f in DEMOGRAPHIC_FIELDS:
            assert columns[f]["type"] == "TEXT" and columns[f]["notnull"] == 0
    assert Repository(repo.path).patient("SYN001") == p


def test_name_dob_matching_and_duplicates(service):
    repo = service.repository
    assert repo.match_patients("  ALEX   dEmO ", date(1990,1,15))[0].id == "SYN001"
    assert not repo.match_patients("Alex",date(1990,1,15))
    assert not repo.match_patients("' OR 1=1 --",date(1990,1,15))
    with repo.connection() as db:
        db.execute("INSERT INTO patients(id,name,date_of_birth) VALUES('dup','Alex Demo','1980-01-01')")
    assert service.match_patient("Alex Demo",date(1990,1,15)).ok
    with repo.connection() as db:
        db.execute("UPDATE patients SET date_of_birth='1990-01-15' WHERE id='dup'")
    ambiguous = service.match_patient("Alex Demo",date(1990,1,15))
    assert not ambiguous.ok and ambiguous.patient is None


def test_missing_dob_never_invented(service):
    with service.repository.connection() as db:
        db.execute("UPDATE patients SET date_of_birth=NULL WHERE id='SYN001'")
    service.repository.seed(date(2030,1,15),service.timezone)
    p = service.repository.patient("SYN001")
    assert p.date_of_birth is None and p.age is None
    assert not service.match_patient("Alex Demo",date(1990,1,15)).ok
    assert not service.match_patient("Alex Demo",None).ok
