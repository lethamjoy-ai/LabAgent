from functools import lru_cache
import hashlib
import secrets
import time
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from .repository import StaffRepository


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@lru_cache(maxsize=1)
def dummy_password_hash():
    # Process-local, random dummy credential only; never cache user credentials.
    return PasswordHasher().hash(secrets.token_urlsafe(32))


class AuthService:
    def __init__(self, database, ttl_seconds=1800, now=None):
        self.database = database
        self.staff = StaffRepository(database)
        self.hasher = PasswordHasher()
        self.ttl = max(1,int(ttl_seconds))
        self.now = now or time.time
        self._dummy_hash = dummy_password_hash()

    def login(self, username, password):
        row = self.staff.by_username(username)
        try:
            self.hasher.verify(row["password_hash"] if row else self._dummy_hash,password)
        except (VerificationError, InvalidHashError):
            return None
        if not row or not row["is_active"]:
            return None
        if self.hasher.check_needs_rehash(row["password_hash"]):
            with self.database.connection() as db:
                db.execute("UPDATE staff SET password_hash=? WHERE id=?",(self.hasher.hash(password),row["id"]))
        token = secrets.token_urlsafe(32)
        with self.database.connection() as db:
            db.execute("INSERT INTO staff_sessions VALUES(?,?,?,?)",(token_hash(token),row["id"],self.now()+self.ttl,self.now()))
        return token

    def current(self, token):
        if not token:
            return None
        with self.database.connection() as db:
            row = db.execute("""SELECT staff.* FROM staff_sessions JOIN staff ON staff.id=staff_sessions.staff_id
                WHERE token_hash=? AND expires>? AND staff.is_active=1""",(token_hash(token),self.now())).fetchone()
        return self.staff.public(row)

    def logout(self, token):
        if token:
            with self.database.connection() as db:
                db.execute("DELETE FROM staff_sessions WHERE token_hash=?",(token_hash(token),))
