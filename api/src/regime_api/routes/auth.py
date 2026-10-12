"""인증 HTTP 진입과 보호 경계. 정책·소유자 판단은 AuthService에 위임한다."""
import json
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, SecretStr, StrictStr
from starlette.concurrency import run_in_threadpool
from regime_api.errors import ApiError, body

router = APIRouter(tags=["auth"])
SESSION_COOKIE = "rl_session"
CSRF_COOKIE = "rl_csrf"
OAUTH_COOKIE = "rl_oauth"

class SocialLogin(BaseModel):
    provider: StrictStr

class Credentials(BaseModel):
    email: StrictStr
    password: SecretStr

class Email(BaseModel):
    email: StrictStr

class Password(BaseModel):
    password: SecretStr

def service(request):
    auth = request.app.state.rl.auth
    if not auth.enabled:
        raise ApiError(503, "auth_disabled", "Account authentication is not configured.")
    return auth

def cookie(response, auth, name, value, age):
    response.set_cookie(name, value, max_age=int(age), httponly=True, secure=auth.site_url.startswith("https://"), samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"

def clear(response, auth):
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=auth.site_url.startswith("https://"), samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"

def client(request):
    return request.client.host if request.client else "unknown"

@router.get("/auth/config")
def config(request: Request, response: Response):
    auth = request.app.state.rl.auth
    if not auth.enabled:
        return {"enabled":False, "guest_allowed":True, "password_min_length":auth.policy["demo_password_min_length"]}
    csrf = auth.csrf()
    cookie(response, auth, CSRF_COOKIE, csrf, auth.policy["csrf_ttl_seconds"])
    social = auth.social_providers()
    return {"enabled":True, "guest_allowed":False, "providers":["email", *[name for name, enabled in social.items() if enabled]], "social_providers":social, "password_min_length":auth.policy["password_min_length"], "csrf_token":csrf}

@router.post("/auth/oauth")
def oauth(data: SocialLogin, request: Request, response: Response):
    auth = service(request)
    fid, url = auth.start_oauth(data.provider, request.cookies.get(OAUTH_COOKIE), client(request))
    cookie(response, auth, OAUTH_COOKIE, fid, auth.policy["oauth_ttl_seconds"])
    return {"url": url}

@router.get("/auth/oauth/callback")
def oauth_callback(request: Request, code: str = "", error: str = ""):
    auth = service(request)
    try:
        sid, _ = auth.finish_oauth(request.cookies.get(OAUTH_COOKIE), code, error, request.cookies.get(SESSION_COOKIE))
    except ApiError:
        response = RedirectResponse(auth.site_url + "/#/login?auth_error=auth_oauth_failed", status_code=303)
    else:
        response = RedirectResponse(auth.site_url + "/#/builder", status_code=303)
        cookie(response, auth, SESSION_COOKIE, sid, auth.policy["session_ttl_seconds"])
    response.delete_cookie(OAUTH_COOKIE, path="/", httponly=True, secure=auth.site_url.startswith("https://"), samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

@router.post("/auth/signup", status_code=202)
def signup(data: Credentials, request: Request):
    service(request).signup(data.email, data.password.get_secret_value(), client(request))
    return {"status":"check_email"}

@router.post("/auth/login")
def login(data: Credentials, request: Request, response: Response):
    auth = service(request)
    sid, user = auth.login(data.email, data.password.get_secret_value(), request.cookies.get(SESSION_COOKIE), client(request))
    cookie(response, auth, SESSION_COOKIE, sid, auth.policy["session_ttl_seconds"])
    return {"user":user}

@router.get("/auth/me")
def me(request: Request, response: Response):
    auth = service(request)
    user = auth.authenticate(request.cookies.get(SESSION_COOKIE))
    response.headers["Cache-Control"] = "no-store"
    return {"user":user.public()}

@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    auth = service(request)
    auth.logout(request.cookies.get(SESSION_COOKIE))
    if request.cookies.get(OAUTH_COOKIE):
        auth.store.take_flow(request.cookies[OAUTH_COOKIE])
    response = Response(status_code=204)
    response.delete_cookie(OAUTH_COOKIE, path="/", httponly=True, secure=auth.site_url.startswith("https://"), samesite="lax")
    clear(response, auth)
    return response

@router.post("/auth/recover", status_code=202)
def recover(data: Email, request: Request):
    service(request).recover(data.email, client(request))
    return {"status":"check_email"}

@router.post("/auth/password", status_code=204)
def password(data: Password, request: Request):
    auth = service(request)
    auth.password(request.cookies.get(SESSION_COOKIE), data.password.get_secret_value())
    response = Response(status_code=204)
    clear(response, auth)
    return response

def confirm_link(request, token_hash, recovery):
    auth = service(request)
    try:
        sid, _ = auth.verify(token_hash, "recovery" if recovery else "email", request.cookies.get(SESSION_COOKIE))
    except ApiError:
        response = RedirectResponse(auth.site_url + "/#/login?auth_error=auth_failed", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    if recovery:
        response = RedirectResponse(auth.site_url + "/#/password", status_code=303)
        cookie(response, auth, SESSION_COOKIE, sid, auth.policy["recovery_ttl_seconds"])
    else:
        auth.logout(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse(auth.site_url + "/#/login?verified=1", status_code=303)
        clear(response, auth)
    return response

@router.get("/auth/confirm")
def confirm(request: Request, token_hash: str = ""):
    return confirm_link(request, token_hash, False)

@router.get("/auth/recovery/confirm")
def confirm_recovery(request: Request, token_hash: str = ""):
    return confirm_link(request, token_hash, True)

def install(app):
    @app.middleware("http")
    async def authentication(request, call_next):
        auth = app.state.rl.auth
        path = request.url.path.rstrip("/")
        if not auth.enabled or not path.startswith("/api/"):
            return await call_next(request)
        try:
            auth.check_request(request.headers.get("origin"), request.headers.get("host", ""), request.cookies.get(CSRF_COOKIE, ""), request.headers.get("x-csrf-token", ""), request.method)
            if path.startswith("/api/auth/"):
                response = await call_next(request)
            else:
                user = await run_in_threadpool(auth.authenticate, request.cookies.get(SESSION_COOKIE))
                request.state.auth_user = user
                payload = None
                if path == "/api/presets" and request.method == "POST":
                    try:
                        payload = await request.json()
                    except (ValueError, UnicodeError):
                        pass
                await run_in_threadpool(auth.authorize, path, request.method, user, payload)
                response = await call_next(request)
                # Only collection/creation/busy responses need JSON transformation.
                if path in {"/api/runs", "/api/searches", "/api/presets"} or response.status_code == 409:
                    content = b"".join([chunk async for chunk in response.body_iterator])
                    try:
                        original = json.loads(content)
                        result = await run_in_threadpool(auth.response, path, request.method, user, response.status_code, original, request.query_params.get("limit", "50"))
                    except (ValueError, UnicodeError):
                        result = None
                    headers = dict(response.headers)
                    headers.pop("content-length", None)
                    if result is not None:
                        response = JSONResponse(result, status_code=response.status_code, headers=headers, background=response.background)
                    else:
                        response = Response(content, status_code=response.status_code, headers=headers, background=response.background)
            response.headers["Cache-Control"] = "no-store"
            if response.status_code == 401:
                clear(response, auth)
            return response
        except ApiError as e:
            response = JSONResponse(body(e.code, e.message, e.detail, e.retryable), status_code=e.status)
            response.headers["Cache-Control"] = "no-store"
            if e.status == 401:
                clear(response, auth)
            return response
