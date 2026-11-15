"""Back up/migrate the configured database and report preservation, without seeding.

Run from project root: python -m scripts.migrate_demographics
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from app.appointments.repository import Repository


def snapshot(path):
    with sqlite3.connect(path) as db:
        statements = {"patients":"SELECT id,name FROM patients ORDER BY id",
            "appointments":"SELECT id,patient_id,test_id,start,status,request_id FROM appointments ORDER BY id",
            "confirmations":"SELECT token,details,request_id,expires FROM confirmations ORDER BY token"}
        return {name:db.execute(sql).fetchall() for name,sql in statements.items()}


def main():
    load_dotenv()
    path = Path(os.getenv("LAB_DATABASE", "data/labassist.sqlite3"))
    if not path.exists():
        raise SystemExit(f"No existing database at {path}; migration did not create a replacement.")
    before = snapshot(path)
    repo = Repository(path)
    after = snapshot(path)
    if before != after:
        raise RuntimeError("Record comparison changed during migration; inspect the backup before proceeding.")
    with repo.connection() as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        schema = [dict(r) for r in db.execute("PRAGMA table_info(patients)")]
    digest = lambda values: hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    report = {"database":str(path.resolve()), **repo.migration, "counts":{k:len(v) for k,v in after.items()},
        "preserved":before == after, "integrity":integrity, "patient_schema":schema,
        "before_sha256":digest(before), "after_sha256":digest(after)}
    output = path.parent / "demographics-migration-report.json"
    # Preserve the first migration report (and its backup reference) on repeated runs.
    if repo.migration["added"] or not output.exists():
        output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
