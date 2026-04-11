"""Auth routes: home, register, login, logout, forgot password, set-language."""
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.config import settings
from app.db import get_db
from app.web import templates
from app.i18n import t
from app.models import User, SessionModel, PasswordResetSession
from app.emails import send_email, render_email_template
from app.codes import create_code, verify_code, get_active_code
from app.security import hash_password, verify_password, validate_password
from app.wallets import create_bsc_wallet_for_user

from app.auth import (
    get_lang_from_request,
    utcnow,
    normalize_email,
    get_user_by_any_email,
    is_entered_email_verified,
    send_login_2fa_code,
    create_session,
    COOKIE_NAME,
    SESSION_TTL_DAYS,
    _enforce_resend_cooldown,
)

router = APIRouter()


class RegisterConfirmIn(BaseModel):
    email: str
    code: str


class Login2FAIn(BaseModel):
    email: str
    code: str


async def _payload(request: Request) -> dict:
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        return await request.json()
    form = await request.form()
    return dict(form)


@router.get("/")
def home(request: Request):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse("index.html", {"request": request, "lang": lang})


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str | None = Form(None),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    is_en = (lang or "").strip().lower() == "en"

    email = normalize_email(email)
    pwd = (password or "").strip()

    if not email:
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "email_required")})
    if not pwd:
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "password_empty")})

    err = validate_password(pwd, lang)
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})

    existing = (
        db.query(User)
        .filter(or_(User.email == email, User.backup_email == email))
        .first()
    )

    if existing:
        if existing.is_email_verified or existing.is_backup_email_verified:
            return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "email_taken")})

        existing.first_name = (first_name or "").strip()
        existing.last_name = (last_name or "").strip()
        existing.phone = (phone.strip() if phone else None)
        existing.password_hash = hash_password(pwd)
        existing.is_active = True
        existing.is_email_verified = False

        db.add(existing)
        db.commit()
        db.refresh(existing)

        try:
            code = get_active_code(existing.id, "registration", db) or create_code(existing.id, "registration", db=db)

            html = render_email_template(
                "emails/registration_code.html",
                {"code": code, "ttl_minutes": 15, "title": "Wild Boar", "lang": lang},
            )
            subject = "Registration confirmation code" if is_en else "Код подтверждения регистрации"
            send_email(existing.email, subject, html)

        except ValueError as e:
            return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, str(e))})
        except Exception:
            return JSONResponse(status_code=500, content={"status": "error", "message": t(lang, "send_email_failed")})

        return JSONResponse(content={"status": "ok", "next": "enter_code", "email": existing.email})

    user = User(
        created_at=utcnow(),
        email=email,
        first_name=(first_name or "").strip(),
        last_name=(last_name or "").strip(),
        phone=(phone.strip() if phone else None),
        password_hash=hash_password(pwd),
        is_active=True,
        is_email_verified=False,
    )

    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "email_taken")})

    db.refresh(user)

    try:
        code = create_code(user.id, "registration", db=db)

        html = render_email_template(
            "emails/registration_code.html",
            {"code": code, "ttl_minutes": 15, "title": "Wild Boar", "lang": lang},
        )
        subject = "Registration confirmation code" if lang == "en" else "Код подтверждения регистрации"
        send_email(user.email, subject, html)

    except Exception:
        db.delete(user)
        db.commit()
        return JSONResponse(status_code=500, content={"status": "error", "message": t(lang, "send_email_failed")})

    return JSONResponse(content={"status": "ok", "next": "enter_code", "email": user.email})


@router.post("/register/confirm")
def register_confirm(request: Request, payload: RegisterConfirmIn, db: Session = Depends(get_db)):
    lang = get_lang_from_request(request)
    code = (payload.code or "").strip()

    user = get_user_by_any_email(db, payload.email)
    if not user:
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "user_not_found")})

    try:
        verify_code(user.id, "registration", code, db=db)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, str(e))})

    try:
        if not user.is_email_verified:
            user.is_email_verified = True

        create_bsc_wallet_for_user(db, user, commit=False)

        session_id = create_session(db, user.id, commit=False)

        db.commit()
    except Exception:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": t(lang, "registration_failed")})

    resp = JSONResponse(content={"status": "ok", "redirect": "/dashboard"})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return resp


@router.post("/register/resend-code")
async def register_resend_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    email = normalize_email(data.get("email"))

    lang = get_lang_from_request(request)
    is_en = (lang or "").strip().lower() == "en"

    if not email:
        return PlainTextResponse(t(lang, "email_required"), status_code=400)

    user = get_user_by_any_email(db, email)
    if not user or not user.is_active or user.is_email_verified:
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        _enforce_resend_cooldown(user.id, "registration")

        code = get_active_code(user.id, "registration", db) or create_code(user.id, "registration", db=db)

        html = render_email_template(
            "emails/registration_code.html",
            {"code": code, "ttl_minutes": 15, "title": "Wild Boar", "lang": lang},
        )
        subject = "Registration confirmation code" if is_en else "Код подтверждения регистрации"
        send_email(user.email, subject, html)

    except ValueError as e:
        return PlainTextResponse(t(lang, str(e)), status_code=400)
    except Exception:
        return PlainTextResponse(t(lang, "send_email_failed"), status_code=500)

    return JSONResponse({"status": "ok"}, status_code=200)


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    lang: str = Form("en"),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    raw_email = email
    pwd = password or ""

    user = get_user_by_any_email(db, raw_email)
    if not user or not user.is_active or not verify_password(pwd, user.password_hash):
        return PlainTextResponse(t(lang, "incorrect_email_or_password"), status_code=401)

    if user.two_factor_enabled:
        ok, err = send_login_2fa_code(db, user, raw_email, lang)
        if not ok:
            return JSONResponse({"status": "error", "message": err}, status_code=400)

        return JSONResponse(content={"status": "2fa_required"})

    session_id = create_session(db, user.id)
    resp = JSONResponse(content={"status": "ok", "redirect": "/dashboard"})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=(request.url.scheme == "https"),
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        path="/",
    )
    return resp


@router.post("/login/2fa/resend")
async def login_2fa_resend(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    entered_email = normalize_email(data.get("email"))

    lang = get_lang_from_request(request)
    is_en = (lang or "").strip().lower() == "en"

    if not entered_email:
        return PlainTextResponse(t(lang, "email_required"), status_code=400)

    user = get_user_by_any_email(db, entered_email)
    if not user or not user.is_active:
        return JSONResponse({"status": "ok"}, status_code=200)

    if entered_email != user.email and entered_email != (user.backup_email or ""):
        return JSONResponse({"status": "ok"}, status_code=200)
    if not is_entered_email_verified(user, entered_email):
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        code = create_code(user.id, "login_2fa", db=db)
        ttl = settings.SECURITY_CODE_TTL_MINUTES
        html = render_email_template(
            "emails/login_2fa_code.html",
            {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
        )
        subject = "Login code" if is_en else "Код для входа"
        send_email(entered_email, subject, html)
    except ValueError as e:
        return PlainTextResponse(t(lang, str(e)), status_code=400)
    except Exception:
        return PlainTextResponse(t(lang, "send_email_failed"), status_code=500)

    return JSONResponse({"status": "ok"}, status_code=200)


@router.post("/login/2fa")
def login_2fa(request: Request, payload: Login2FAIn, db: Session = Depends(get_db)):
    lang = get_lang_from_request(request)
    entered_email = normalize_email(payload.email)
    code = (payload.code or "").strip()

    user = get_user_by_any_email(db, entered_email)
    if not user or not user.is_active:
        return PlainTextResponse(t(lang, "invalid_code"), status_code=400)

    if entered_email != user.email and entered_email != (user.backup_email or ""):
        return PlainTextResponse(t(lang, "invalid_code"), status_code=400)
    if not is_entered_email_verified(user, entered_email):
        return PlainTextResponse(t(lang, "email_not_verified"), status_code=400)

    try:
        verify_code(user.id, "login_2fa", code, db=db)
    except ValueError as e:
        return PlainTextResponse(t(lang, str(e)), status_code=400)

    session_id = create_session(db, user.id)
    resp = JSONResponse(content={"status": "ok", "redirect": "/dashboard"})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=(request.url.scheme == "https"),
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        path="/",
    )
    return resp


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        db.query(SessionModel).filter(SessionModel.id == session_id).delete()
        db.commit()

    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


@router.get("/forgot", response_class=HTMLResponse)
def forgot_password(request: Request):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse("forgot.html", {"request": request, "lang": lang})


@router.post("/forgot/send-code")
async def forgot_send_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    lang = get_lang_from_request(request)

    raw_email = data.get("email") or ""
    email_norm = normalize_email(raw_email)

    user = get_user_by_any_email(db, raw_email)
    if not user or not user.is_active:
        return JSONResponse({"status": "ok", "message": t(lang, "code_sent_if_exists")}, status_code=200)

    if email_norm != user.email and email_norm != (user.backup_email or ""):
        return JSONResponse(
            {
                "status": "error",
                "message": ("Invalid email" if lang == "en" else "Неверная почта"),
            },
            status_code=400,
        )

    if not is_entered_email_verified(user, email_norm):
        return JSONResponse(
            {
                "status": "error",
                "message": ("This email is not verified" if lang == "en" else "Эта почта не подтверждена"),
            },
            status_code=400,
        )

    try:
        code = create_code(user.id, "reset", db=db)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    ttl = settings.SECURITY_CODE_TTL_MINUTES
    html = render_email_template(
        "emails/reset_password_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )

    subject = "WildBoar — password reset code" if lang == "en" else "WildBoar — код для восстановления пароля"

    try:
        send_email(email_norm, subject, html)
    except Exception:
        return PlainTextResponse(t(lang, "send_email_failed"), status_code=400)

    return JSONResponse({"status": "ok"}, status_code=200)


@router.post("/forgot/verify")
async def forgot_verify_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    email = normalize_email(data.get("email"))
    code = (data.get("code") or "").strip()
    lang = get_lang_from_request(request)

    user = get_user_by_any_email(db, email)
    if not user or not user.is_active:
        return PlainTextResponse(t(lang, "invalid_code"), status_code=400)

    try:
        ok = verify_code(user.id, "reset", code, db=db)
        if not ok:
            return PlainTextResponse(t(lang, "invalid_code"), status_code=400)
    except ValueError as e:
        return PlainTextResponse(t(lang, str(e)), status_code=400)

    token = uuid.uuid4().hex
    now = utcnow()

    reset_session = PasswordResetSession(
        id=token,
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
        is_used=False,
    )
    db.add(reset_session)
    db.commit()

    return JSONResponse({"status": "ok", "redirect": f"/forgot/new-password?token={token}"}, status_code=200)


@router.get("/forgot/new-password", response_class=HTMLResponse)
def forgot_new_password(request: Request, token: str = Query(default=""), db: Session = Depends(get_db)):
    lang = get_lang_from_request(request)
    token = (token or "").strip()
    now = utcnow()

    rs = db.query(PasswordResetSession).filter(PasswordResetSession.id == token).one_or_none()
    if not rs or rs.is_used or rs.expires_at <= now:
        return templates.TemplateResponse(
            "forgot_new_password.html",
            {"request": request, "token": None, "error": t(lang, "link_expired"), "lang": lang},
        )

    return templates.TemplateResponse(
        "forgot_new_password.html",
        {"request": request, "token": token, "error": None, "lang": lang},
    )


@router.post("/forgot/new-password")
async def forgot_set_new_password(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    token = (data.get("token") or "").strip()
    password = (data.get("password") or "")
    password_confirm = (data.get("password_confirm") or "")
    lang = get_lang_from_request(request)

    if password != password_confirm:
        return PlainTextResponse(t(lang, "passwords_do_not_match"), status_code=400)

    err = validate_password(password, lang)
    if err:
        return PlainTextResponse(err, status_code=400)

    now = utcnow()
    rs = db.query(PasswordResetSession).filter(PasswordResetSession.id == token).one_or_none()
    if not rs or rs.is_used or rs.expires_at <= now:
        return PlainTextResponse(t(lang, "link_expired"), status_code=400)

    user = db.query(User).filter(User.id == rs.user_id).one_or_none()
    if not user:
        return PlainTextResponse(t(lang, "user_not_found"), status_code=400)

    user.password_hash = hash_password(password)
    rs.is_used = True

    db.query(SessionModel).filter(SessionModel.user_id == user.id).delete(synchronize_session=False)

    db.commit()
    return JSONResponse({"status": "ok", "redirect": "/"}, status_code=200)
