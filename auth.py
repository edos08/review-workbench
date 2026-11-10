"""User accounts and sessions. Standard library only.

Accounts live in DATA_DIR/users.json (password hashes only, scrypt with a per-user salt).
A session is a signed cookie: `<user id>.<expiry>.<generation>.<signature>`, signed with a random secret kept in
DATA_DIR/.secret. Changing a password bumps the user's generation, which signs out every other session.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 200
SESSION_SECONDS = 30 * 24 * 3600
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
COOKIE = "rw_session"


class AuthError(Exception):
    """Carries an HTTP status so the server can pass it straight on."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _hash(password: str, salt: bytes, n, r, p) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = _hash(password, salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        got = _hash(password, bytes.fromhex(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, bytes.fromhex(want))
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password("not-a-real-password")  # burns the same time when the username does not exist


class Users:
    # failed logins allowed per username inside the window before further attempts are refused
    MAX_FAILURES = 8
    WINDOW = 300

    def __init__(self, data_dir, allow_registration=True):
        self.path = Path(data_dir) / "users.json"
        self.allow_registration = allow_registration
        self._lock = threading.Lock()
        self._failures = {}
        secret_path = Path(data_dir) / ".secret"
        if not secret_path.is_file():
            tmp = secret_path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(32))
            os.replace(tmp, secret_path)
        self._secret = secret_path.read_bytes()

    # ---------- storage ----------
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, users: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(users, indent=2).encode())
        os.replace(tmp, self.path)

    def count(self) -> int:
        return len(self._load())

    def can_register(self) -> bool:
        """The very first account is always allowed, otherwise nobody could ever sign in."""
        return self.allow_registration or self.count() == 0

    @staticmethod
    def public(u: dict) -> dict:
        return {"id": u["id"], "username": u["username"]}

    # ---------- accounts ----------
    @staticmethod
    def _check_password_rules(password) -> str:
        if not isinstance(password, str) or len(password) < MIN_PASSWORD:
            raise AuthError(400, f"Password must be at least {MIN_PASSWORD} characters.")
        if len(password) > MAX_PASSWORD:
            raise AuthError(400, "Password is too long.")
        return password

    def register(self, username, password) -> tuple:
        """Returns (public user, is_first_account)."""
        if not isinstance(username, str) or not USERNAME_RE.match(username):
            raise AuthError(400, "Username must be 3-32 characters: letters, digits, dot, dash or underscore.")
        self._check_password_rules(password)
        hashed = hash_password(password)  # slow on purpose; done outside the lock
        with self._lock:
            users = self._load()
            if not (self.allow_registration or not users):
                raise AuthError(403, "Registration is closed on this server.")
            if any(u["username"].lower() == username.lower() for u in users.values()):
                raise AuthError(409, "That username is taken.")
            uid = secrets.token_hex(8)
            users[uid] = {"id": uid, "username": username, "hash": hashed, "gen": 0, "created": time.time()}
            self._save(users)
            return self.public(users[uid]), len(users) == 1

    def login(self, username, password) -> dict:
        key = str(username).lower()
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self.WINDOW]
            self._failures[key] = recent
            if len(recent) >= self.MAX_FAILURES:
                raise AuthError(429, "Too many failed attempts. Wait a few minutes and try again.")
        user = None
        if isinstance(username, str) and isinstance(password, str) and len(password) <= MAX_PASSWORD:
            user = next((u for u in self._load().values() if u["username"].lower() == key), None)
        ok = check_password(password if isinstance(password, str) and len(password) <= MAX_PASSWORD else "", user["hash"] if user else _DUMMY)
        if not (user and ok):
            with self._lock:
                self._failures.setdefault(key, []).append(now)
            raise AuthError(401, "Wrong username or password.")
        with self._lock:
            self._failures.pop(key, None)
        return self.public(user)

    def change_password(self, uid, old, new) -> None:
        self._check_password_rules(new)
        with self._lock:
            users = self._load()
            u = users.get(uid)
            if not u or not isinstance(old, str) or len(old) > MAX_PASSWORD or not check_password(old, u["hash"]):
                raise AuthError(401, "Current password is wrong.")
            u["hash"] = hash_password(new)
            u["gen"] = int(u.get("gen", 0)) + 1
            self._save(users)

    def reset_password(self, username, new) -> None:
        """Administrator action (see the command line below): set a new password and sign every session out."""
        self._check_password_rules(new)
        with self._lock:
            users = self._load()
            u = next((u for u in users.values() if u["username"].lower() == str(username).lower()), None)
            if not u:
                raise AuthError(404, f"No user called {username!r}.")
            u["hash"] = hash_password(new)
            u["gen"] = int(u.get("gen", 0)) + 1
            self._save(users)

    def usernames(self) -> list:
        return sorted(u["username"] for u in self._load().values())

    # ---------- sessions ----------
    def _sign(self, payload: str) -> str:
        return base64.urlsafe_b64encode(hmac.new(self._secret, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")

    def issue(self, user: dict) -> str:
        gen = int(self._load().get(user["id"], {}).get("gen", 0))
        payload = f"{user['id']}.{int(time.time()) + SESSION_SECONDS}.{gen}"
        return f"{payload}.{self._sign(payload)}"

    def from_token(self, token):
        """The public user a valid, unexpired session cookie belongs to, else None."""
        try:
            uid, exp, gen, sig = token.split(".")
            if not hmac.compare_digest(sig, self._sign(f"{uid}.{exp}.{gen}")) or int(exp) < time.time():
                return None
            u = self._load().get(uid)
            if not u or int(u.get("gen", 0)) != int(gen):
                return None
            return self.public(u)
        except (ValueError, AttributeError):
            return None


def _cli(argv):
    """python3 auth.py list | reset <username>   (run it where DATA_DIR is set, e.g. inside the container)"""
    import getpass
    import sys
    users = Users(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent / "data")
    if argv[:1] == ["list"]:
        print("\n".join(users.usernames()) or "(no users yet)")
    elif len(argv) == 2 and argv[0] == "reset":
        pw = os.environ.get("NEW_PASSWORD") or getpass.getpass(f"New password for {argv[1]}: ")
        try:
            users.reset_password(argv[1], pw)
        except AuthError as e:
            sys.exit(str(e))
        print(f"Password for {argv[1]} changed; all their sessions were signed out.")
    else:
        sys.exit("usage: python3 auth.py list | reset <username>")


if __name__ == "__main__":
    import sys
    _cli(sys.argv[1:])
