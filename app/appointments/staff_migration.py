"""Backed-up, transactional replacement of the former global slot constraint."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def migrate_staffing(repository, existing, previous_backup=None):
    report = {"changed":False, "backup":None}
    with repository.connection() as db:
        columns = {r["name"] for r in db.execute("PRAGMA table_info(appointments)")}
        if "staff_id" in columns:
            return report
        if columns != {"id","patient_id","test_id","start","status","request_id"}:
            raise RuntimeError("Unexpected appointment schema; manual migration review required.")
        if existing:
            if previous_backup:
                report["backup"] = previous_backup
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = Path(repository.path).parent / "backups" / f"{Path(repository.path).stem}.before-staff.{stamp}.sqlite3"
                backup.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(backup) as target:
                    db.backup(target)
                report["backup"] = str(backup.resolve())
        db.execute("BEGIN IMMEDIATE")
        try:
            if "staff_id" in {r["name"] for r in db.execute("PRAGMA table_info(appointments)")}:
                db.rollback()
                return report
            db.execute("""CREATE TABLE staff(
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Staff','Admin')),
                is_active INTEGER NOT NULL CHECK(is_active IN (0,1)))""")
            db.execute("""CREATE TABLE staff_hours(
                staff_id TEXT NOT NULL REFERENCES staff(id), weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
                start_time TEXT NOT NULL, end_time TEXT NOT NULL,
                PRIMARY KEY(staff_id,weekday), CHECK(start_time < end_time))""")
            db.execute("""CREATE TABLE staff_sessions(
                token_hash TEXT PRIMARY KEY, staff_id TEXT NOT NULL REFERENCES staff(id),
                expires REAL NOT NULL, created REAL NOT NULL)""")
            db.execute("""CREATE TABLE appointments_staff(
                id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id),
                test_id TEXT NOT NULL, start TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('scheduled','completed','cancelled')),
                request_id TEXT NOT NULL UNIQUE, staff_id TEXT REFERENCES staff(id))""")
            db.execute("""INSERT INTO appointments_staff(id,patient_id,test_id,start,status,request_id)
                SELECT id,patient_id,test_id,start,status,request_id FROM appointments""")
            db.execute("DROP TABLE appointments")
            db.execute("ALTER TABLE appointments_staff RENAME TO appointments")
            db.execute("CREATE UNIQUE INDEX unique_staff_slot ON appointments(staff_id,start) WHERE staff_id IS NOT NULL")
            db.execute("ALTER TABLE confirmations ADD COLUMN actor_staff_id TEXT REFERENCES staff(id)")
            db.execute("ALTER TABLE confirmations ADD COLUMN session_hash TEXT")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Foreign key integrity failed during staffing migration.")
            db.commit()
            report["changed"] = True
        except Exception:
            db.rollback()
            raise
    return report
