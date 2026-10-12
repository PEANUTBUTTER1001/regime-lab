"""Supabase Auth HTTPS adapter. 연결 키와 공급사 본문을 오류에 포함하지 않는다."""
import httpx
import logging
from uuid import UUID
from urllib.parse import urlencode
from regime_api.auth.contracts import Tokens, User
from regime_api.errors import ApiError

class AuthAccessLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if "/api/auth/" in str(args[2]):
                args[2] = str(args[2]).split("?", 1)[0]
                record.args = tuple(args)
        return True

class SupabaseProvider:
    def __init__(self, url: str, key: str, timeout: float, site_url: str, client=None):
        self.url, self._key, self.site_url = url.rstrip("/"), key, site_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self):
        self.client.close()

    def _request(self, method, path, payload=None, token=None):
        headers = {"apikey": self._key}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            r = self.client.request(method, self.url + "/auth/v1/" + path, headers=headers, json=payload)
        except httpx.HTTPError:
            raise ApiError(503, "auth_unavailable", "Authentication is temporarily unavailable.", retryable=True) from None
        if r.status_code >= 400:
            if r.status_code == 429:
                raise ApiError(429, "auth_rate_limited", "Please wait before trying again.", retryable=True)
            if r.status_code >= 500:
                raise ApiError(503, "auth_unavailable", "Authentication is temporarily unavailable.", retryable=True)
            raise ApiError(401, "auth_failed", "The credentials or verification link are invalid.")
        try:
            return r.json() if r.content else {}
        except ValueError:
            raise ApiError(503, "auth_unavailable", "Authentication is temporarily unavailable.", retryable=True) from None

    @staticmethod
    def _user(data):
        try:
            uid = str(UUID(data["id"]))
            identities = data.get("identities", [])
            if not isinstance(identities, list):
                raise ValueError()
            providers = tuple(sorted({item.get("provider") for item in identities
                                      if isinstance(item, dict) and item.get("provider") in {"google", "kakao"}}))
            email = data.get("email")
            if (email is not None and not isinstance(email, str)) or (not email and not providers):
                raise ValueError()
            return User(uid, email or None, bool(data.get("email_confirmed_at")), providers)
        except (KeyError, TypeError, ValueError):
            raise ApiError(503, "auth_unavailable", "Authentication is temporarily unavailable.", retryable=True) from None

    def _tokens(self, data):
        try:
            user = self._user(data["user"])
            access, refresh = data["access_token"], data["refresh_token"]
            ttl = float(data["expires_in"])
            if not access or not refresh or ttl <= 0:
                raise ValueError()
            return Tokens(user, access, refresh, ttl)
        except (KeyError, TypeError, ValueError):
            raise ApiError(503, "auth_unavailable", "Authentication is temporarily unavailable.", retryable=True) from None

    def social_providers(self):
        data = self._request("GET", "settings")
        external = data.get("external", {}) if isinstance(data, dict) else {}
        if not isinstance(external, dict):
            raise ApiError(503, "auth_unavailable", "Authentication is temporarily unavailable.")
        return {provider: external.get(provider) is True for provider in ("google", "kakao")}

    def oauth_url(self, provider, challenge, callback):
        if provider not in {"google", "kakao"}:
            raise ApiError(422, "auth_oauth_provider", "Unsupported sign-in provider.")
        return self.url + "/auth/v1/authorize?" + urlencode({
            "provider": provider, "redirect_to": callback,
            "code_challenge": challenge, "code_challenge_method": "s256",
        })

    def exchange(self, code, verifier):
        return self._tokens(self._request("POST", "token?grant_type=pkce",
                                         {"auth_code": code, "code_verifier": verifier}))

    def signup(self, email, password):
        self._request("POST", "signup", {"email": email, "password": password})

    def login(self, email, password):
        return self._tokens(self._request("POST", "token?grant_type=password", {"email": email, "password": password}))

    def verify(self, token_hash, kind):
        return self._tokens(self._request("POST", "verify", {"token_hash": token_hash, "type": kind}))

    def refresh(self, token):
        return self._tokens(self._request("POST", "token?grant_type=refresh_token", {"refresh_token": token}))

    def user(self, token):
        return self._user(self._request("GET", "user", token=token))

    def recover(self, email):
        self._request("POST", "recover", {"email": email})

    def password(self, token, password):
        self._request("PUT", "user", {"password": password}, token=token)

    def logout(self, token, scope="local"):
        self._request("POST", "logout?scope=" + scope, token=token)
