from .models import Staff
from app.appointments.repository import normalize_name


class StaffRepository:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def public(row):
        return Staff(**{k:row[k] for k in Staff.model_fields}) if row else None

    def by_username(self, username):
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM staff WHERE username=? COLLATE NOCASE",(username.strip(),)).fetchone()
        return dict(row) if row else None

    def get(self, identifier):
        with self.database.connection() as db:
            return self.public(db.execute("SELECT * FROM staff WHERE id=?",(identifier,)).fetchone())

    def active(self):
        with self.database.connection() as db:
            return [self.public(r) for r in db.execute("SELECT * FROM staff WHERE is_active=1 ORDER BY first_name,last_name,id")]

    def match(self, name):
        name = normalize_name(name)
        return [p for p in self.active() if name in (normalize_name(p.name), normalize_name(p.first_name))]

    def working_hours(self, staff_id, weekday):
        with self.database.connection() as db:
            row = db.execute("SELECT start_time,end_time FROM staff_hours WHERE staff_id=? AND weekday=?",(staff_id,weekday)).fetchone()
        return tuple(row) if row else None

    def put_account(self, identifier, username, password_hash, first_name, last_name, role="Staff", active=False):
        with self.database.connection() as db:
            db.execute("INSERT INTO staff VALUES(?,?,?,?,?,?,?)",(identifier,username,password_hash,first_name,last_name,role,int(active)))

    def activate_password(self, identifier, password_hash):
        with self.database.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("UPDATE staff SET password_hash=?,is_active=1 WHERE id=?",(password_hash,identifier))
                db.execute("DELETE FROM staff_sessions WHERE staff_id=?",(identifier,))
                db.commit()
            except Exception:
                db.rollback()
                raise

    def set_hours(self, identifier, weekday, start, end):
        from datetime import time
        beginning, ending = time.fromisoformat(start), time.fromisoformat(end)
        if beginning >= ending or beginning.second or ending.second or beginning.minute not in (0,30) or ending.minute not in (0,30):
            raise ValueError("Working hours must use ordered 30 minute boundaries.")
        with self.database.connection() as db:
            db.execute("INSERT INTO staff_hours VALUES(?,?,?,?) ON CONFLICT(staff_id,weekday) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time",
                (identifier,weekday,beginning.strftime("%H:%M"),ending.strftime("%H:%M")))

    def replace_hours(self, identifier, weekdays, start, end):
        from datetime import time
        beginning, ending = time.fromisoformat(start), time.fromisoformat(end)
        if (not all(type(day) is int and 0 <= day <= 6 for day in weekdays) or beginning >= ending or
                beginning.second or ending.second or beginning.minute not in (0,30) or ending.minute not in (0,30)):
            raise ValueError("Invalid weekly working-hours configuration.")
        with self.database.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM staff_hours WHERE staff_id=?",(identifier,))
                db.executemany("INSERT INTO staff_hours VALUES(?,?,?,?)",[(identifier,day,beginning.strftime("%H:%M"),ending.strftime("%H:%M")) for day in set(weekdays)])
                db.commit()
            except Exception:
                db.rollback()
                raise
