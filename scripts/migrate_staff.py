"""Migrate existing data without assigning historical appointments or seeding."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from app.appointments.repository import Repository


def snapshot(path):
    with sqlite3.connect(path) as db:
        return {"patients":db.execute("SELECT * FROM patients ORDER BY id").fetchall(),
            "appointments":db.execute("SELECT id,patient_id,test_id,start,status,request_id FROM appointments ORDER BY id").fetchall(),
            "confirmations":db.execute("SELECT token,details,request_id,expires FROM confirmations ORDER BY token").fetchall()}


def main():
    load_dotenv()
    path = Path(os.getenv("LAB_DATABASE","data/labassist.sqlite3"))
    if not path.exists():
        raise SystemExit("No existing database found; run the local staff setup for a new database.")
    before = snapshot(path)
    repository = Repository(path)
    after = snapshot(path)
    if before != after:
        raise RuntimeError("Record comparison changed. Review the migration backup.")
    with repository.connection() as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = list(db.execute("PRAGMA foreign_key_check"))
        unassigned = db.execute("SELECT count(*) FROM appointments WHERE staff_id IS NULL").fetchone()[0]
    digest = lambda rows: hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    report = {"database":str(path.resolve()),**repository.staff_migration,"counts":{k:len(v) for k,v in after.items()},
        "preserved":before==after,"unassigned_appointments":unassigned,"integrity":integrity,"foreign_key_errors":len(foreign_keys),
        "before_sha256":digest(before),"after_sha256":digest(after)}
    output = path.parent / "staff-migration-report.json"
    if repository.staff_migration["changed"] or not output.exists():
        output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
