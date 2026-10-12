"""계정·세션·접근 권한 유스케이스. 시계·난수·저장소·공급사를 주입받는다."""
import hashlib
import hmac
from urllib.parse import urlsplit
from regime_api.auth.contracts import Session, Provider, Store
from regime_api.errors import ApiError

class AuthService:
    def __init__(self, provider: Provider | None, store: Store | None, policy: dict, site_url, admins, now, nonce, csrf_secret, enabled=True, lock=None):
        self.provider, self.store, self.policy = provider, store, policy
        self.site_url = site_url.rstrip("/")
        self.admins, self.now, self.nonce, self.secret = frozenset(admins), now, nonce, csrf_secret
        self.enabled = enabled
        self._limits = {}
        self._lock = lock

    def csrf(self):
        text = str(int(self.now())) + "." + self.nonce()
        return text + "." + hmac.new(self.secret, text.encode(), hashlib.sha256).hexdigest()

    def check_request(self, origin, host, csrf_cookie, csrf_header, method):
        if not self.enabled:
            return
        expected = urlsplit(self.site_url)
        if host.lower() != expected.netloc.lower():
            raise ApiError(403, "auth_origin", "This request origin is not allowed.")
        if method in {"GET", "HEAD", "OPTIONS"}:
            return
        if origin != self.site_url:
            raise ApiError(403, "auth_origin", "This request origin is not allowed.")
        try:
            text, sig = csrf_header.rsplit(".", 1)
            stamp = int(text.split(".", 1)[0])
            valid = hmac.compare_digest(csrf_cookie, csrf_header) and hmac.compare_digest(sig, hmac.new(self.secret, text.encode(), hashlib.sha256).hexdigest())
            age = self.now() - stamp
            if not valid or age < 0 or age > self.policy["csrf_ttl_seconds"]:
                raise ValueError()
        except (AttributeError, ValueError, TypeError):
            raise ApiError(403, "auth_csrf", "Please reload the page and try again.") from None

    def check_password(self, password):
        if not self.policy["password_min_length"] <= len(password) <= self.policy["password_max_length"]:
            raise ApiError(422, "auth_password", "The password does not meet the configured length requirements.")

    def check_email(self, email):
        email = email.strip().lower()
        if len(email) > self.policy["email_max_length"] or email.count("@") != 1 or not all(email.rsplit("@", 1)) or any(c.isspace() for c in email):
            raise ApiError(422, "auth_email", "Enter a valid email address.")
        return email

    def rate(self, kind, who):
        now = self.now()
        with self._lock:
            self._limits = {k:v for k,v in self._limits.items() if v[1] > now}
            key = (kind, who)
            count, until = self._limits.get(key, (0, now + self.policy["rate_window_seconds"]))
            if count >= self.policy["rate_max_attempts"]:
                raise ApiError(429, "auth_rate_limited", "Please wait before trying again.", retryable=True)
            self._limits[key] = (count + 1, until)

    def issue(self, tokens, old_sid=None, purpose="login"):
        if not tokens.user.confirmed:
            raise ApiError(401, "auth_unconfirmed", "Verify your email before signing in.")
        now = self.now()
        self.store.prune(now - self.policy["idle_ttl_seconds"])
        sid = self.nonce()
        try:
            self.store.put(sid, Session(tokens, now, now, now + tokens.expires_in, purpose))
        except Exception:
            raise ApiError(503, "auth_unavailable", "Authentication is temporarily unavailable.", retryable=True) from None
        if old_sid:
            self.store.drop(old_sid)
        return sid, tokens.user.public()

    def login(self, email, password, old_sid, client):
        self.rate("login", client)
        return self.issue(self.provider.login(self.check_email(email), password), old_sid)

    def signup(self, email, password, client):
        self.rate("mail", client)
        self.check_password(password)
        try:
            self.provider.signup(self.check_email(email), password)
        except ApiError as e:
            if e.status not in {400, 401, 422}:
                raise

    def recover(self, email, client):
        self.rate("mail", client)
        try:
            self.provider.recover(self.check_email(email))
        except ApiError as e:
            if e.status not in {400, 401, 422}:
                raise

    def verify(self, token_hash, kind, old_sid):
        if not token_hash or len(token_hash) > self.policy["token_max_length"]:
            raise ApiError(401, "auth_failed", "The verification link is invalid.")
        tokens = self.provider.verify(token_hash, kind)
        if kind == "recovery":
            return self.issue(tokens, old_sid, "recovery")
        self.provider.logout(tokens.access)
        return None, None

    def authenticate(self, sid, purpose="login"):
        if not sid or len(sid) > self.policy["token_max_length"]:
            raise ApiError(401, "authentication_required", "Sign in to continue.")
        with self.store.locked(sid):
            session = self.store.get(sid)
            now = self.now()
            if session is None:
                raise ApiError(401, "authentication_required", "Sign in to continue.")
            ttl = self.policy["recovery_ttl_seconds"] if session.purpose == "recovery" else self.policy["session_ttl_seconds"]
            if now - session.created >= ttl or now - session.seen >= self.policy["idle_ttl_seconds"] or session.purpose != purpose:
                self.store.drop(sid)
                raise ApiError(401, "authentication_required", "Sign in to continue.")
            try:
                if now >= session.access_until:
                    tokens = self.provider.refresh(session.tokens.refresh)
                    if tokens.user.id != session.tokens.user.id:
                        raise ApiError(401, "authentication_required", "Sign in to continue.")
                    session.tokens = tokens
                    session.access_until = now + tokens.expires_in
                user = self.provider.user(session.tokens.access)
                if user.id != session.tokens.user.id or not user.confirmed:
                    raise ApiError(401, "authentication_required", "Sign in to continue.")
            except ApiError as e:
                if e.status == 401:
                    self.store.drop(sid)
                raise
            session.seen = now
            return user

    def logout(self, sid):
        session = self.store.get(sid) if sid else None
        if sid:
            self.store.drop(sid)
        if session:
            try:
                self.provider.logout(session.tokens.access)
            except ApiError:
                pass

    def password(self, sid, password):
        user = self.authenticate(sid, "recovery")
        self.check_password(password)
        session = self.store.get(sid)
        self.provider.password(session.tokens.access, password)
        self.store.drop_user(user.id)
        try:
            self.provider.logout(session.tokens.access, "global")
        except ApiError:
            pass

    def owns(self, kind, rid, uid):
        try:
            return self.store.owner(kind, rid) == uid
        except Exception:
            raise ApiError(503, "auth_unavailable", "Access permissions are temporarily unavailable.", retryable=True) from None

    def require_owner(self, kind, rid, uid):
        if not self.owns(kind, rid, uid):
            raise ApiError(404, "not_found", "Resource not found.")

    def authorize(self, path, method, user, payload=None):
        if path.startswith("/api/llm/config") and user.id not in self.admins:
            raise ApiError(403, "forbidden", "Only administrators can manage shared model settings.")
        parts = path.strip("/").split("/")
        kinds = {"runs":"run", "searches":"search", "presets":"preset"}
        if len(parts) >= 3 and parts[1] in kinds and not (parts[1] == "searches" and parts[2] in {"options", "preview"}):
            self.require_owner(kinds[parts[1]], parts[2], user.id)
        if path == "/api/presets" and method == "POST" and isinstance(payload, dict):
            source = payload.get("from_search")
            if isinstance(source, dict):
                self.require_owner("search", str(source.get("search_id", "")), user.id)
        if path.endswith("/report") and method == "POST":
            self.rate("report", user.id)

    def response(self, path, method, user, status, payload, limit):
        if not isinstance(payload, dict):
            return payload
        create = {"/api/runs":("run","run_id"), "/api/searches":("search","search_id"), "/api/presets":("preset","id")}
        if method == "POST" and path in create and 200 <= status < 300:
            kind, key = create[path]
            rid = payload.get(key)
            if not isinstance(rid, str):
                raise ApiError(503, "auth_unavailable", "Resource ownership could not be recorded.")
            try:
                self.store.claim(kind, rid, user.id)
            except Exception:
                raise ApiError(503, "auth_unavailable", "Resource ownership could not be recorded.", retryable=True) from None
        if status == 409 and payload.get("code") == "busy":
            detail = payload.get("detail") or {}
            kind = detail.get("kind", "run")
            rid = detail.get("id") or detail.get("run_id")
            if not self.owns(kind, rid, user.id):
                return {**payload, "detail": None}
        lists = {"/api/runs":("runs","run","run_id"), "/api/searches":("searches","search","search_id"), "/api/presets":("presets","preset","id")}
        if method == "GET" and path in lists and status == 200:
            key, kind, id_key = lists[path]
            rows = [r for r in payload.get(key, []) if self.owns(kind, r.get(id_key), user.id)]
            if key != "presets":
                rows = rows[:max(0, min(int(limit), self.policy["list_limit"]))]
            return {**payload, key:rows}
        return payload
