"""All SQL lives here. One collection station; fixed 30 minute visits."""
import sqlite3
import unicodedata
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from .models import Appointment, Patient

DEMOGRAPHIC_FIELDS = ("date_of_birth", "gender", "address_line1", "city", "state", "country", "postal_code")


def normalize_name(name):
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


class Repository:
    @classmethod
    def open_existing(cls, path):
        """Open explicitly provisioned storage without DDL, migration or seeding."""
        if not Path(path).is_file():
            raise FileNotFoundError("Appointment storage is not provisioned.")
        repository = cls.__new__(cls)
        repository.path = str(Path(path).resolve())
        return repository

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        existing = Path(path).exists() and Path(path).stat().st_size > 0
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS appointments(
              id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id),
              test_id TEXT NOT NULL, start TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('scheduled','completed','cancelled')),
              request_id TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS confirmations(
              token TEXT PRIMARY KEY, details TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,
              expires REAL NOT NULL);
            """)
        self.migration = self.migrate_demographics(existing)
        from .staff_migration import migrate_staffing
        self.staff_migration = migrate_staffing(self, existing, self.migration["backup"])

    def migrate_demographics(self, backup_existing=True):
        with self.connection() as db:
            fields = {r["name"] for r in db.execute("PRAGMA table_info(patients)")}
            missing = [f for f in DEMOGRAPHIC_FIELDS if f not in fields]
            report = {"added": [], "backup": None}
            if not missing:
                return report
            if backup_existing:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = Path(self.path).parent / "backups" / f"{Path(self.path).stem}.before-demographics.{stamp}.sqlite3"
                backup.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(backup) as target:
                    db.backup(target)
                report["backup"] = str(backup.resolve())
            db.execute("BEGIN IMMEDIATE")
            try:
                fields = {r["name"] for r in db.execute("PRAGMA table_info(patients)")}
                for field in DEMOGRAPHIC_FIELDS:
                    if field not in fields:
                        # Column identifiers come exclusively from the static allowlist above.
                        db.execute(f"ALTER TABLE patients ADD COLUMN {field} TEXT")
                        report["added"].append(field)
                db.commit()
            except Exception:
                db.rollback()
                raise
            return report

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.create_function("normalized_name", 1, normalize_name, deterministic=True)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def patient(self, identifier):
        with self.connection() as db:
            row = db.execute("SELECT * FROM patients WHERE id=?", (identifier,)).fetchone()
        return Patient(**dict(row)) if row else None

    def match_patients(self, name, dob):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM patients WHERE normalized_name(name)=? AND date_of_birth=?",
                (normalize_name(name), dob.isoformat())).fetchall()
        return [Patient(**dict(r)) for r in rows]

    def patient_appointments(self, patient_id):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM appointments WHERE patient_id=? ORDER BY start", (patient_id,)).fetchall()
        return sorted([Appointment(**dict(r)) for r in rows], key=lambda a: a.start)

    def appointments(self):
        with self.connection() as db:
            return [Appointment(**dict(r)) for r in db.execute("SELECT * FROM appointments ORDER BY start")]

    def seed(self, day, timezone):
        from datetime import datetime, time
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # DOBs belong only to newly inserted, explicitly synthetic fixtures.
                # Existing patients, including NULL demographics, are never backfilled.
                db.executemany("INSERT OR IGNORE INTO patients(id,name,date_of_birth) VALUES(?,?,?)", [
                    ("SYN001", "Alex Demo", "1990-01-15"), ("SYN002", "Jordan Sample", "1988-04-20"),
                    ("SYN003", "Taylor Example", "1995-06-10")])
                for hour, patient, test, status in [(9,"SYN001","CBC","scheduled"), (10,"SYN002","GLU","completed")]:
                    start = datetime.combine(day, time(hour), timezone).isoformat()
                    db.execute("INSERT OR IGNORE INTO appointments(id,patient_id,test_id,start,status,request_id) VALUES(?,?,?,?,?,?)",
                        (f"DEMO-{day}-{hour}", patient, test, start, status, f"seed-{day}-{hour}"))
                db.commit()
            except Exception:
                db.rollback()
                raise
