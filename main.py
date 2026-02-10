import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy import create_engine, String, Text, BigInteger, Boolean, DateTime, ForeignKey, func, or_, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base, Mapped, mapped_column
from sqlalchemy.exc import IntegrityError
from typing import Generator
import bcrypt
from pydantic import BaseModel

from email_service import send_email, render_email_template
from codes import create_code, verify_code
from i18n import t


# -----------------------
# DB config
# -----------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is required. Set it in environment or in .env. "
        "Example: postgresql://postgres:changeme@localhost:5432/WildBoar_platform"
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


# -----------------------
# Pydantic models
# -----------------------
class RegisterConfirmIn(BaseModel):
    email: str
    code: str


class Login2FAIn(BaseModel):
    email: str
    code: str


class PasswordChangeRequest(BaseModel):
    new_password: str
    slot: int  # 1 или 2


class PasswordChangeConfirm(BaseModel):
    new_password: str
    code: str


class EmailSendCodePayload(BaseModel):
    slot: int
    email: str


class EmailConfirmPayload(BaseModel):
    slot: int
    code: str


class EmailDeletePayload(BaseModel):
    slot: int


# -----------------------
# ORM models (public.users, public.sessions)
# -----------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False, default="basic")

    backup_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_backup_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # session_id token
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserWallet(Base):
    __tablename__ = "user_wallets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    blockchain: Mapped[str] = mapped_column(String(32), nullable=False, default="BSC")
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_private_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class PasswordResetSession(Base):
    __tablename__ = "password_reset_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# -----------------------
# Auth / settings
# -----------------------

SESSION_TTL_DAYS = 30
COOKIE_NAME = "session_id"
SUPPORTED_LANGS = {"ru", "en"}
DEFAULT_LANG = "ru"
LANG_COOKIE_NAME = "lang"


class NotAuthenticated(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def validate_password(pwd: str, lang: str | None = None) -> str | None:
    if len(pwd) < 8:
        return t(lang, "password_min_length")
    if re.search(r"\s", pwd):
        return t(lang, "password_no_spaces")
    if not re.search(r"\d", pwd):
        return t(lang, "password_digit")
    if not re.search(r"[a-zа-я]", pwd):
        return t(lang, "password_lower")
    if not re.search(r"[A-ZА-Я]", pwd):
        return t(lang, "password_upper")
    if not re.search(r"[^A-Za-zА-Яа-я0-9]", pwd):
        return t(lang, "password_special")
    return None


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def get_user_by_any_email(db: Session, email: str) -> User | None:
    email = normalize_email(email)
    return (
        db.query(User)
        .filter(
            or_(
                User.email == email,
                User.backup_email == email,
            )
        )
        .first()
    )


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def email_taken_by_other_user(db: Session, email_norm: str, current_user_id: int) -> bool:
    q = (
        db.query(User.id)
        .filter(
            User.id != current_user_id,
            or_(
                User.email == email_norm,
                User.backup_email == email_norm,
            ),
        )
        .first()
    )
    return q is not None


def is_slot_email_verified(user: User, slot: int) -> bool:
    if slot == 1:
        return bool(user.is_email_verified)
    return bool(user.is_backup_email_verified)


def get_slot_email(user: User, slot: int) -> str | None:
    return user.email if slot == 1 else user.backup_email


def _is_entered_email_verified(user: User, entered_email_norm: str) -> bool:
    if entered_email_norm == user.email:
        return bool(user.is_email_verified)
    if user.backup_email and entered_email_norm == user.backup_email:
        return bool(user.is_backup_email_verified)
    return False


def send_login_2fa_code(db: Session, user: User, raw_email: str, lang: str) -> tuple[bool, str]:
    """
    1) проверяет, что введённый email принадлежит user (email или backup_email)
    2) проверяет, что именно эта почта подтверждена
    3) создаёт код purpose=login_2fa
    4) отправляет письмо НА ВВЕДЁННЫЙ EMAIL
    Возвращает (ok, error_message)
    """
    to_email = normalize_email(raw_email)

    # email должен быть одной из почт пользователя
    if to_email != user.email and to_email != (user.backup_email or ""):
        return False, ("Invalid email" if lang == "en" else "Неверная почта")

    # именно введённая почта должна быть подтверждена
    if not _is_entered_email_verified(user, to_email):
        return False, ("This email is not verified" if lang == "en" else "Эта почта не подтверждена")

    try:
        code = create_code(user.id, "login_2fa", db=db)
    except ValueError as e:
        # codes.py в 4.1 кидает ключи (code_cooldown/...)
        return False, t(lang, str(e)) if "t" in globals() else str(e)

    ttl = int(os.getenv("SECURITY_CODE_TTL_MINUTES", "15"))
    html = render_email_template(
        "emails/login_2fa_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )

    subject = "WildBoar — login confirmation code" if lang == "en" else "WildBoar — код подтверждения входа"

    try:
        send_email(to_email, subject, html)
    except Exception:
        return False, ("Failed to send email" if lang == "en" else "Не удалось отправить письмо")

    return True, ""


async def _payload(request: Request) -> dict:
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        return await request.json()
    form = await request.form()
    return dict(form)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_session(db: Session, user_id: int, commit: bool = True) -> str:
    # cleanup expired sessions
    db.query(SessionModel).filter(SessionModel.expires_at < utcnow()).delete(synchronize_session=False)

    session_id = uuid.uuid4().hex
    expires_at = utcnow() + timedelta(days=SESSION_TTL_DAYS)

    db.add(SessionModel(id=session_id, user_id=user_id, created_at=utcnow(), expires_at=expires_at))

    if commit:
        db.commit()
    else:
        db.flush()

    return session_id


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise NotAuthenticated()

    sess = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not sess:
        raise NotAuthenticated()

    if sess.expires_at <= utcnow():
        db.delete(sess)
        db.commit()
        raise NotAuthenticated()

    user = db.query(User).filter(User.id == sess.user_id).first()
    if not user or not user.is_active:
        raise NotAuthenticated()

    return user


def get_lang_from_request(request: Request) -> str:
    lang = request.cookies.get(LANG_COOKIE_NAME, DEFAULT_LANG)
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    return lang


# -----------------------
# FastAPI app + templates/static
# -----------------------
app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(NotAuthenticated)
def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/", status_code=302)


# -----------------------
# Routes
# -----------------------
@app.get("/")
def home(request: Request):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse("index.html", {"request": request, "lang": lang})


@app.get("/useragreement", response_class=HTMLResponse)
def useragreement(request: Request):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse("useragreement.html", {"request": request, "lang": lang})


@app.post("/register")
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

    # нормализация (обязательно — иначе дубли по регистру)
    email = normalize_email(email)
    pwd = (password or "").strip()

    # базовые проверки
    if not email:
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "email_required")})
    if not pwd:
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "password_empty")})

    # пароль (у тебя уже есть validate_password)
    err = validate_password(pwd, lang)
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})

    # email уникален (учитываем и основной, и резервный слоты)
    existing = (
        db.query(User)
        .filter(or_(User.email == email, User.backup_email == email))
        .first()
    )
    if existing:
        return JSONResponse(status_code=400, content={"status": "error", "message": t(lang, "email_taken")})

    # создаём пользователя (НЕ верифицирован)
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

    # создаём код
    try:
        code = create_code(user.id, "registration", db=db)

        html = render_email_template(
            "emails/registration_code.html",
            {"code": code, "ttl_minutes": 15, "title": "Wild Boar", "lang": lang},
        )
        subject = "Registration confirmation code" if lang == "en" else "Код подтверждения регистрации"
        send_email(user.email, subject, html)

    except Exception as e:
        # если письмо не ушло — чтобы не блокировать повторную регистрацию,
        # удаляем пользователя (FK CASCADE удалит его коды)
        db.delete(user)
        db.commit()
        return JSONResponse(status_code=500, content={"status": "error", "message": t(lang, "send_email_failed")})

    return JSONResponse(content={"status": "ok", "next": "enter_code", "email": user.email})


@app.post("/register/confirm")
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

    # подтверждаем email + создаём кошелёк + создаём сессию (атомарно)
    try:
        if not user.is_email_verified:
            user.is_email_verified = True

        from wallets import create_bsc_wallet_for_user
        create_bsc_wallet_for_user(db, user, commit=False)

        session_id = create_session(db, user.id, commit=False)

        db.commit()
    except Exception:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": t(lang, "registration_failed")})

    # cookie после успешного commit
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


@app.post("/register/resend-code")
async def register_resend_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    email = normalize_email(data.get("email"))

    lang = get_lang_from_request(request)
    is_en = (lang or "").strip().lower() == "en"

    if not email:
        return PlainTextResponse(t(lang, "email_required"), status_code=400)

    user = get_user_by_any_email(db, email)
    # не светим состояние пользователя
    if not user or not user.is_active or user.is_email_verified:
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        code = create_code(user.id, "registration", db=db)
        html = render_email_template(
            "emails/registration_code.html",
            {"code": code, "ttl_minutes": 15, "title": "Wild Boar", "lang": lang},
        )
        subject = "Registration confirmation code" if lang == "en" else "Код подтверждения регистрации"
        send_email(user.email, subject, html)
    except ValueError as e:
        # cooldown и прочие валидационные ошибки — текстом
        return PlainTextResponse(t(lang, str(e)), status_code=400)
    except Exception:
        return PlainTextResponse(t(lang, "send_email_failed"), status_code=500)

    return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/set-language")
async def set_language(request: Request):
    data = await request.json()
    lang = (data.get("lang") or "").lower()
    if lang not in SUPPORTED_LANGS:
        return JSONResponse({"status": "error", "message": "Unsupported language"}, status_code=400)

    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(
        key=LANG_COOKIE_NAME,
        value=lang,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        path="/",
    )
    return resp


@app.get("/settings/security")
def security_settings_page(request: Request, db: Session = Depends(get_db)):
    try:
        user = get_current_user(request, db)
    except NotAuthenticated:
        return RedirectResponse("/", status_code=303)

    lang = get_lang_from_request(request)
    emails = [
        {"slot": 1, "email": user.email, "verified": user.is_email_verified},
        {"slot": 2, "email": user.backup_email, "verified": user.is_backup_email_verified},
    ]
    return templates.TemplateResponse(
        "security_settings.html",
        {"request": request, "lang": lang, "user": user, "emails": emails, "account_type": user.account_type},
    )


@app.post("/settings/security/emails/send-code")
def emails_send_code(
    payload: EmailSendCodePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user(request, db)
    except NotAuthenticated:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    lang = get_lang_from_request(request)

    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    email_norm = normalize_email(payload.email)
    if not is_valid_email(email_norm):
        return JSONResponse({"status": "error", "message": t(lang, "invalid_email_format")}, status_code=400)

    # запретить дубликат в другом слоте этого же пользователя
    other_email = user.backup_email if slot == 1 else user.email
    if other_email and email_norm == other_email:
        return JSONResponse({"status": "error", "message": t(lang, "email_already_used")}, status_code=400)

    # уникальность по всей БД (email и backup_email у других пользователей)
    if email_taken_by_other_user(db, email_norm, user.id):
        return JSONResponse({"status": "error", "message": t(lang, "email_already_used")}, status_code=400)

    # записываем email в нужный слот + сбрасываем verified
    if slot == 1:
        user.email = email_norm
        user.is_email_verified = False
    else:
        user.backup_email = email_norm
        user.is_backup_email_verified = False

    db.add(user)
    db.commit()

    # создаём код (используем purpose="registration" для подтверждения почты)
    try:
        code = create_code(user.id, "registration", db=db)
    except ValueError as e:
        # если недавно уже создавали код (code_cooldown) — повторно шлём ПРЕДЫДУЩИЙ код
        if str(e) == "code_cooldown":
            row = db.execute(
                text(
                    """
                    SELECT code
                    FROM public.security_codes
                    WHERE user_id = :user_id
                      AND purpose = :purpose
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user.id, "purpose": "registration"},
            ).mappings().first()
            if not row:
                return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)
            code = row["code"]
        else:
            return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    ttl = int(os.getenv("SECURITY_CODE_TTL_MINUTES", "15"))
    html = render_email_template(
        "emails/email_verify_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )

    subject = "WildBoar — email confirmation" if lang == "en" else "WildBoar — подтверждение почты"

    try:
        send_email(email_norm, subject, html)
    except Exception:
        return JSONResponse({"status": "error", "message": t(lang, "send_email_failed")}, status_code=500)

    return {"status": "ok"}


@app.post("/settings/security/emails/confirm")
def emails_confirm(
    payload: EmailConfirmPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user(request, db)
    except NotAuthenticated:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    lang = get_lang_from_request(request)

    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    current_email = get_slot_email(user, slot)
    if not current_email:
        return JSONResponse({"status": "error", "message": t(lang, "email_slot_empty")}, status_code=400)

    code = (payload.code or "").strip()

    try:
        verify_code(user.id, "registration", code, db=db)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    if slot == 1:
        user.is_email_verified = True
    else:
        user.is_backup_email_verified = True

    db.add(user)
    db.commit()

    return {"status": "ok"}


@app.post("/settings/security/emails/delete")
def emails_delete(
    payload: EmailDeletePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user(request, db)
    except NotAuthenticated:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    lang = get_lang_from_request(request)

    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    non_empty = 0
    if user.email:
        non_empty += 1
    if user.backup_email:
        non_empty += 1

    if non_empty <= 1:
        return JSONResponse({"status": "error", "message": t(lang, "cannot_delete_last_email")}, status_code=400)

    if slot == 1:
        # переносим вторую почту в первую
        user.email = user.backup_email
        user.is_email_verified = user.is_backup_email_verified
        user.backup_email = None
        user.is_backup_email_verified = False
    else:
        user.backup_email = None
        user.is_backup_email_verified = False

    db.add(user)
    db.commit()

    return {"status": "ok"}


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    lang: str = Form("ru"),
    db: Session = Depends(get_db),
):
    # язык берём из куки/запроса
    lang = get_lang_from_request(request)
    raw_email = email
    pwd = password or ""

    # ищем по любой почте (основной или резервной)
    user = get_user_by_any_email(db, raw_email)
    if not user or not user.is_active or not verify_password(pwd, user.password_hash):
        return PlainTextResponse(t(lang, "incorrect_email_or_password"), status_code=401)

    # 2FA включена (сейчас по умолчанию у всех True)
    if user.two_factor_enabled:
        ok, err = send_login_2fa_code(db, user, raw_email, lang)
        if not ok:
            return JSONResponse({"status": "error", "message": err}, status_code=400)

        # фронт может показать форму ввода кода
        return JSONResponse(content={"status": "2fa_required"})

    # 2FA отключена (на будущее): сразу создаём сессию
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


@app.post("/login/2fa/resend")
async def login_2fa_resend(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    entered_email = normalize_email(data.get("email"))

    lang = get_lang_from_request(request)
    is_en = (lang or "").strip().lower() == "en"

    if not entered_email:
        return PlainTextResponse(t(lang, "email_required"), status_code=400)

    user = get_user_by_any_email(db, entered_email)
    # не светим состояние пользователя
    if not user or not user.is_active:
        return JSONResponse({"status": "ok"}, status_code=200)

    # проверяем именно введённую почту (слот 1 или слот 2) + verified
    if entered_email != user.email and entered_email != (user.backup_email or ""):
        return JSONResponse({"status": "ok"}, status_code=200)
    if not _is_entered_email_verified(user, entered_email):
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        code = create_code(user.id, "login_2fa", db=db)
        ttl = int(os.getenv("SECURITY_CODE_TTL_MINUTES", "15"))
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


@app.post("/login/2fa")
def login_2fa(request: Request, payload: Login2FAIn, db: Session = Depends(get_db)):
    lang = get_lang_from_request(request)
    entered_email = normalize_email(payload.email)
    code = (payload.code or "").strip()

    user = get_user_by_any_email(db, entered_email)
    if not user or not user.is_active:
        return PlainTextResponse(t(lang, "invalid_code"), status_code=400)

    # проверяем, что именно введённая почта подтверждена (слот 1 или 2)
    if entered_email != user.email and entered_email != (user.backup_email or ""):
        return PlainTextResponse(t(lang, "invalid_code"), status_code=400)
    if not _is_entered_email_verified(user, entered_email):
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


@app.post("/settings/security/send-code")
def send_password_change_code(
    payload: PasswordChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user(request, db)
    except NotAuthenticated:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    lang = get_lang_from_request(request)

    # 1) валидируем пароль как раньше (используй свою текущую validate_password)
    new_pwd = (payload.new_password or "").strip()
    err = validate_password(new_pwd, lang)
    if err:
        return JSONResponse({"status": "error", "message": err}, status_code=400)

    # 2) выбираем email по slot и проверяем, что он непустой и подтверждённый
    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    if slot == 1:
        to_email = user.email
        is_verified = bool(user.is_email_verified)
    else:
        to_email = user.backup_email
        is_verified = bool(user.is_backup_email_verified)

    if not to_email:
        return JSONResponse({"status": "error", "message": t(lang, "email_slot_empty")}, status_code=400)
    if not is_verified:
        return JSONResponse({"status": "error", "message": t(lang, "email_not_verified")}, status_code=400)

    # 3) создаём код и шлём письмо на выбранный email
    try:
        code = create_code(user.id, "password_change", db=db)
    except ValueError as e:
        # если сработал минутный лимит (code_cooldown) — повторно шлём последний код
        if str(e) == "code_cooldown":
            row = db.execute(
                text(
                    """
                    SELECT code
                    FROM public.security_codes
                    WHERE user_id = :user_id
                      AND purpose = :purpose
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user.id, "purpose": "password_change"},
            ).mappings().first()
            if not row:
                return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)
            code = row["code"]
        else:
            return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    ttl = int(os.getenv("SECURITY_CODE_TTL_MINUTES", "15"))
    html = render_email_template(
        "emails/password_change_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )

    subject = "WildBoar — password change confirmation" if lang == "en" else "WildBoar — подтверждение смены пароля"

    try:
        send_email(to_email, subject, html)
    except Exception:
        return JSONResponse({"status": "error", "message": t(lang, "send_email_failed")}, status_code=500)

    return {"status": "ok"}


@app.post("/settings/security/change-password")
def change_password_confirm(
    payload: PasswordChangeConfirm,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user(request, db)
    except NotAuthenticated:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    lang = get_lang_from_request(request)
    new_pwd = (payload.new_password or "").strip()
    err = validate_password(new_pwd, lang)
    if err:
        return JSONResponse({"status": "error", "message": err}, status_code=400)

    code = (payload.code or "").strip()
    try:
        verify_code(user.id, "password_change", code, db=db)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    # меняем пароль
    user.password_hash = hash_password(new_pwd)

    # инвалидируем остальные сессии пользователя (оставляем текущую)
    current_sid = request.cookies.get(COOKIE_NAME)
    q = db.query(SessionModel).filter(SessionModel.user_id == user.id)
    if current_sid:
        q = q.filter(SessionModel.id != current_sid)
    q.delete(synchronize_session=False)

    db.commit()
    return {"status": "ok"}


@app.get("/dashboard")
def dashboard(request: Request, user: User = Depends(get_current_user)):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "lang": lang,
            "user": user,
            "account_type": user.account_type,
        },
    )



@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        db.query(SessionModel).filter(SessionModel.id == session_id).delete()
        db.commit()

    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


@app.get("/forgot", response_class=HTMLResponse)
def forgot_password(request: Request):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse("forgot.html", {"request": request, "lang": lang})


@app.post("/forgot/send-code")
async def forgot_send_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    lang = get_lang_from_request(request)

    raw_email = data.get("email") or ""
    email_norm = normalize_email(raw_email)

    # всегда отвечаем 200, чтобы не светить наличие пользователя
    user = get_user_by_any_email(db, raw_email)
    if not user or not user.is_active:
        return JSONResponse({"status": "ok", "message": t(lang, "code_sent_if_exists")}, status_code=200)

    # email должен быть одной из почт пользователя
    if email_norm != user.email and email_norm != (user.backup_email or ""):
        return JSONResponse(
            {
                "status": "error",
                "message": ("Invalid email" if lang == "en" else "Неверная почта"),
            },
            status_code=400,
        )

    # и именно эта почта должна быть подтверждена
    if not _is_entered_email_verified(user, email_norm):
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

    ttl = int(os.getenv("SECURITY_CODE_TTL_MINUTES", "15"))
    html = render_email_template(
        "emails/reset_password_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )

    subject = "WildBoar — password reset code" if lang == "en" else "WildBoar — код для восстановления пароля"

    try:
        send_email(email_norm, subject, html)
    except Exception:
        # тут можно вернуть 200 (не палить), но по UX лучше сообщить ошибку
        return PlainTextResponse(t(lang, "send_email_failed"), status_code=400)

    return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/forgot/verify")
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


@app.get("/forgot/new-password", response_class=HTMLResponse)
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


@app.post("/forgot/new-password")
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

    # инвалидируем все обычные сессии пользователя
    db.query(SessionModel).filter(SessionModel.user_id == user.id).delete(synchronize_session=False)

    db.commit()
    return JSONResponse({"status": "ok", "redirect": "/"}, status_code=200)
