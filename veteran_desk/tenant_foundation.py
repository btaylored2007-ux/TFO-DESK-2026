"""Dormant customer-account foundation. Not mounted on the owner desk.

Nothing in the live dashboard imports this module. It does not open a port,
read provider credentials, or copy the owner paper book. A store exists only
when a caller passes enabled=True and a root outside the live tree.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid

ENABLED_BY_DEFAULT = False
LIVE_PREFIXES = ("/opt/tfo/live",)
MIN_PASSWORD = 12
BLOCKED_PASSWORDS = frozenset({
    "password", "password1234", "changeme", "changeme123", "admin", "admin12345",
    "default", "default12345", "letmein1234", "qwertyuiop12",
})
ROLES = ("viewer", "member", "tenant_admin")
RANK = {"viewer": 1, "member": 2, "tenant_admin": 3}
ACTION_ROLE = {
    "read_paper": "viewer",
    "read_upload": "viewer",
    "read_audit": "viewer",
    "write_note": "member",
    "upload": "member",
    "manage_members": "tenant_admin",
}
SESSION_SECONDS = 12 * 3600
UPLOAD_MAX = 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{7,40}$")
_UPLOAD = re.compile(r"^up-[0-9a-f]{32}$")


class FoundationOff(PermissionError):
    pass


class Denied(PermissionError):
    pass


def _hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex() + ":" + derived.hex()


def _check_password(password, stored):
    salt_hex, _, derived = str(stored).partition(":")
    if not salt_hex or not derived:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = _hash_password(password, salt).split(":", 1)[1]
    return hmac.compare_digest(candidate, derived)


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _reject_password(password):
    text = str(password or "")
    if len(text) < MIN_PASSWORD or text.lower() in BLOCKED_PASSWORDS:
        raise Denied("password does not meet the account rule")


def assert_separate_root(root):
    real = os.path.realpath(root)
    for prefix in LIVE_PREFIXES:
        if real == prefix or real.startswith(prefix + os.sep):
            raise Denied("customer store cannot use the owner live tree")
    book = os.path.join(real, "veteran_desk", "book.py")
    if os.path.isfile(book):
        raise Denied("customer store cannot contain the owner book")
    return real


class Store:
    def __init__(self, root, *, enabled=False):
        if ENABLED_BY_DEFAULT or enabled is not True:
            raise FoundationOff("customer foundation is off")
        self.root = assert_separate_root(root)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        for name in ("accounts.json", "sessions.json"):
            path = os.path.join(self.root, name)
            if not os.path.exists(path):
                self._atomic(path, {})

    def _path(self, *parts):
        return os.path.join(self.root, *parts)

    def _atomic(self, path, value):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _read(self, path, default):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return default

    def _audit(self, tenant_id, user_id, action, allowed):
        directory = self._path("tenants", tenant_id)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, "audit.jsonl")
        row = dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   tenant_id=tenant_id, user_id=user_id, action=action, allowed=bool(allowed))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def provision_tenant(self, email, password, display_name):
        """Operator entry point. There is no public registration route."""
        _reject_password(password)
        email = str(email or "").strip().lower()
        if "@" not in email or len(email) > 120:
            raise Denied("email is not usable")
        accounts = self._read(self._path("accounts.json"), {})
        if email in accounts:
            raise Denied("account already exists")
        tenant_id = "tn_" + uuid.uuid4().hex[:16]
        user_id = "us_" + uuid.uuid4().hex[:16]
        accounts[email] = dict(user_id=user_id, tenant_id=tenant_id, role="tenant_admin",
                               password=_hash_password(password), display_name=str(display_name or "")[:80])
        self._atomic(self._path("accounts.json"), accounts)
        paper = dict(paper_only=True, broker=None, imported_owner_book=False, positions=[], notes=[])
        self._atomic(self._path("tenants", tenant_id, "paper.json"), paper)
        os.makedirs(self._path("tenants", tenant_id, "uploads"), mode=0o700, exist_ok=True)
        self._audit(tenant_id, user_id, "provision", True)
        return dict(email=email, user_id=user_id, tenant_id=tenant_id, role="tenant_admin")

    def add_member(self, token, email, password, role):
        ctx = self.context(token)
        self._require(ctx, "manage_members")
        if role not in ("viewer", "member"):
            raise Denied("a tenant admin cannot grant another admin")
        _reject_password(password)
        email = str(email or "").strip().lower()
        accounts = self._read(self._path("accounts.json"), {})
        if email in accounts:
            raise Denied("account already exists")
        user_id = "us_" + uuid.uuid4().hex[:16]
        accounts[email] = dict(user_id=user_id, tenant_id=ctx["tenant_id"], role=role,
                               password=_hash_password(password), display_name="")
        self._atomic(self._path("accounts.json"), accounts)
        self._audit(ctx["tenant_id"], ctx["user_id"], "add_member", True)
        return dict(email=email, user_id=user_id, tenant_id=ctx["tenant_id"], role=role)

    def login(self, email, password):
        accounts = self._read(self._path("accounts.json"), {})
        row = accounts.get(str(email or "").strip().lower())
        if not row or not _check_password(password, row.get("password")):
            raise Denied("login refused")
        token = secrets.token_urlsafe(32)
        sessions = self._read(self._path("sessions.json"), {})
        sessions[_token_hash(token)] = dict(
            user_id=row["user_id"], tenant_id=row["tenant_id"], role=row["role"],
            expires=time.time() + SESSION_SECONDS)
        self._atomic(self._path("sessions.json"), sessions)
        self._audit(row["tenant_id"], row["user_id"], "login", True)
        return token

    def context(self, token):
        sessions = self._read(self._path("sessions.json"), {})
        row = sessions.get(_token_hash(str(token or "")))
        if not row or float(row.get("expires") or 0) < time.time():
            raise Denied("session refused")
        if row.get("role") not in ROLES or not _ID.match(str(row.get("tenant_id") or "")):
            raise Denied("session refused")
        return dict(user_id=row["user_id"], tenant_id=row["tenant_id"], role=row["role"])

    def _require(self, ctx, action):
        needed = ACTION_ROLE[action]
        if RANK[ctx["role"]] < RANK[needed]:
            self._audit(ctx["tenant_id"], ctx["user_id"], action, False)
            raise Denied("role cannot " + action)
        return ctx

    def _own_tenant(self, ctx, claimed_tenant):
        if claimed_tenant not in (None, "", ctx["tenant_id"]):
            self._audit(ctx["tenant_id"], ctx["user_id"], "cross_tenant", False)
            raise Denied("tenant does not match the session")
        return ctx["tenant_id"]

    def paper(self, token, claimed_tenant=None):
        ctx = self._require(self.context(token), "read_paper")
        tenant_id = self._own_tenant(ctx, claimed_tenant)
        return self._read(self._path("tenants", tenant_id, "paper.json"), {})

    def write_note(self, token, text, claimed_tenant=None):
        ctx = self._require(self.context(token), "write_note")
        tenant_id = self._own_tenant(ctx, claimed_tenant)
        path = self._path("tenants", tenant_id, "paper.json")
        paper = self._read(path, {})
        notes = list(paper.get("notes") or [])
        notes.append(dict(by=ctx["user_id"], text=str(text or "")[:500]))
        paper.update(paper_only=True, broker=None, imported_owner_book=False, notes=notes[-50:])
        self._atomic(path, paper)
        self._audit(tenant_id, ctx["user_id"], "write_note", True)
        return paper

    def upload(self, token, filename, data, claimed_tenant=None):
        ctx = self._require(self.context(token), "upload")
        tenant_id = self._own_tenant(ctx, claimed_tenant)
        raw = bytes(data or b"")
        if not raw or len(raw) > UPLOAD_MAX or "/" in str(filename) or ".." in str(filename):
            raise Denied("upload refused")
        upload_id = "up-" + secrets.token_hex(16)
        directory = self._path("tenants", tenant_id, "uploads")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, upload_id)
        with open(path, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)
        self._audit(tenant_id, ctx["user_id"], "upload", True)
        return dict(id=upload_id, tenant_id=tenant_id, bytes=len(raw))

    def read_upload(self, token, upload_id, claimed_tenant=None):
        ctx = self._require(self.context(token), "read_upload")
        tenant_id = self._own_tenant(ctx, claimed_tenant)
        if not _UPLOAD.match(str(upload_id or "")):
            raise Denied("upload refused")
        directory = os.path.realpath(self._path("tenants", tenant_id, "uploads"))
        path = os.path.realpath(os.path.join(directory, upload_id))
        if not path.startswith(directory + os.sep) or not os.path.isfile(path):
            raise Denied("upload refused")
        with open(path, "rb") as handle:
            return handle.read()

    def audit(self, token, claimed_tenant=None):
        ctx = self._require(self.context(token), "read_audit")
        tenant_id = self._own_tenant(ctx, claimed_tenant)
        path = self._path("tenants", tenant_id, "audit.jsonl")
        rows = []
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        rows.append(json.loads(line))
        except (OSError, ValueError):
            return rows
        return rows
