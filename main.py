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

from sqlalchemy import create_engine, String, BigInteger, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, declarative_base, Mapped, mapped_column
from sqlalchemy.exc import IntegrityError
from typing import Generator
import bcrypt
from pydantic import BaseModel

from email_service import send_email, render_email_template
from codes import create_code, verify_code


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


def validate_password(pwd: str) -> str | None:
    if len(pwd) < 8:
        return "Пароль должен содержать не менее 8 символов."
    if re.search(r"\s", pwd):
        return "Пароль не должен содержать пробелы."
    if not re.search(r"\d", pwd):
        return "Пароль должен содержать минимум одну цифру."
    if not re.search(r"[a-zа-я]", pwd):
        return "Пароль должен содержать минимум одну строчную букву."
    if not re.search(r"[A-ZА-Я]", pwd):
        return "Пароль должен содержать минимум одну заглавную букву."
    if not re.search(r"[^A-Za-zА-Яа-я0-9]", pwd):
        return "Пароль должен содержать минимум один спецсимвол."
    return None


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


def create_session(db: Session, user_id: int) -> str:
    # cleanup expired sessions (simple, no cron)
    db.query(SessionModel).filter(SessionModel.expires_at < utcnow()).delete(synchronize_session=False)

    session_id = uuid.uuid4().hex
    expires_at = utcnow() + timedelta(days=SESSION_TTL_DAYS)

    db.add(SessionModel(id=session_id, user_id=user_id, created_at=utcnow(), expires_at=expires_at))
    db.commit()
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
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/useragreement", response_class=HTMLResponse)
def useragreement(request: Request):
    return templates.TemplateResponse("useragreement.html", {"request": request})


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
    # нормализация
    email_norm = (email or "").strip().lower()
    pwd = (password or "").strip()

    # базовые проверки
    if not email_norm:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Email обязателен"})
    if not pwd:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Пароль не должен быть пустым"})

    # пароль (у тебя уже есть validate_password)
    err = validate_password(pwd)
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})

    # email уникален
    existing = db.query(User).filter(User.email == email_norm).first()
    if existing:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Email уже занят"})

    # создаём пользователя (НЕ верифицирован)
    user = User(
        created_at=utcnow(),
        email=email_norm,
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
        return JSONResponse(status_code=400, content={"status": "error", "message": "Email уже занят"})

    db.refresh(user)

    # создаём код
    try:
        code = create_code(user.id, "registration", db=db)

        html = render_email_template(
            "emails/registration_code.html",
            {"code": code, "ttl_minutes": 15, "title": "Wild Boar"},
        )
        send_email(user.email, "Код подтверждения регистрации", html)

    except Exception as e:
        # если письмо не ушло — чтобы не блокировать повторную регистрацию,
        # удаляем пользователя (FK CASCADE удалит его коды)
        db.delete(user)
        db.commit()
        return JSONResponse(status_code=500, content={"status": "error", "message": "Не удалось отправить письмо"})

    return JSONResponse(content={"status": "ok", "next": "enter_code", "email": user.email})


@app.post("/register/confirm")
def register_confirm(payload: RegisterConfirmIn, db: Session = Depends(get_db)):
    email_norm = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()

    user = db.query(User).filter(User.email == email_norm).first()
    if not user:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Пользователь не найден"})

    try:
        verify_code(user.id, "registration", code, db=db)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})

    # подтверждаем email (если уже подтвержден — тоже ок)
    if not user.is_email_verified:
        user.is_email_verified = True
        db.commit()

    # создаём сессию и ставим cookie
    session_id = create_session(db, user.id)
    resp = JSONResponse(content={"status": "ok", "redirect": "/dashboard"})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=False,  # на https поставишь True
        path="/",
    )
    return resp


@app.post("/register/resend-code")
async def register_resend_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    email = (data.get("email") or "").strip().lower()

    if not email:
        return PlainTextResponse("Email обязателен", status_code=400)

    user = db.query(User).filter(User.email == email).first()
    # не светим состояние пользователя
    if not user or not user.is_active or user.is_email_verified:
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        code = create_code(user.id, "registration", db=db)
        html = render_email_template(
            "emails/registration_code.html",
            {"code": code, "ttl_minutes": 15, "title": "Wild Boar"},
        )
        send_email(user.email, "Код подтверждения регистрации", html)
    except ValueError as e:
        # cooldown и прочие валидационные ошибки — текстом
        return PlainTextResponse(str(e), status_code=400)
    except Exception:
        return PlainTextResponse("Не удалось отправить письмо", status_code=500)

    return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email_norm = (email or "").strip().lower()
    pwd = password or ""

    user = db.query(User).filter(User.email == email_norm).first()
    if not user or not user.is_active or not verify_password(pwd, user.password_hash):
        return PlainTextResponse("Неверный email или пароль", status_code=401)

    if not user.is_email_verified:
        return PlainTextResponse("Email не подтверждён. Завершите регистрацию.", status_code=400)

    # 2FA включена (сейчас по умолчанию у всех True)
    if user.two_factor_enabled:
        try:
            code = create_code(user.id, "login_2fa", db=db)
            html = render_email_template(
                "emails/login_2fa_code.html",
                {"code": code, "ttl_minutes": 15, "title": "Wild Boar"},
            )
            send_email(user.email, "Код для входа", html)
        except Exception:
            return PlainTextResponse("Не удалось отправить письмо", status_code=500)

        return JSONResponse(content={"status": "2fa_required"})

    # 2FA отключена (на будущее)
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
    email = (data.get("email") or "").strip().lower()

    if not email:
        return PlainTextResponse("Email обязателен", status_code=400)

    user = db.query(User).filter(User.email == email).first()
    # не светим состояние пользователя
    if not user or not user.is_active or not user.is_email_verified:
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        code = create_code(user.id, "login_2fa", db=db)
        html = render_email_template(
            "emails/login_2fa_code.html",
            {"code": code, "ttl_minutes": 15, "title": "Wild Boar"},
        )
        send_email(user.email, "Код для входа", html)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=400)
    except Exception:
        return PlainTextResponse("Не удалось отправить письмо", status_code=500)

    return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/login/2fa")
def login_2fa(payload: Login2FAIn, request: Request, db: Session = Depends(get_db)):
    email_norm = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()

    user = db.query(User).filter(User.email == email_norm).first()
    if not user or not user.is_active:
        return PlainTextResponse("Неверный код", status_code=400)

    if not user.is_email_verified:
        return PlainTextResponse("Email не подтверждён. Завершите регистрацию.", status_code=400)

    try:
        verify_code(user.id, "login_2fa", code, db=db)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=400)

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


@app.get("/dashboard")
def dashboard(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})



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
    return templates.TemplateResponse("forgot.html", {"request": request})


@app.post("/forgot/send-code")
async def forgot_send_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    email = (data.get("email") or "").strip().lower()

    # всегда отвечаем 200, чтобы не светить наличие пользователя
    user = db.query(User).filter(User.email == email).one_or_none()
    if not user or not user.is_active:
        return JSONResponse({"status": "ok", "message": "Если такой email существует, код отправлен"}, status_code=200)

    try:
        code = create_code(user.id, "reset", db=db)
        html = render_email_template(
            "emails/reset_password_code.html",
            {"code": code, "ttl_minutes": int(os.getenv("SECURITY_CODE_TTL_MINUTES", "15")), "title": "Wild Boar"},
        )
        send_email(user.email, "Код для сброса пароля", html)
    except Exception:
        # тут можно вернуть 200 (не палить), но по UX лучше сообщить ошибку
        return PlainTextResponse("Не удалось отправить письмо", status_code=400)

    return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/forgot/verify")
async def forgot_verify_code(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()

    user = db.query(User).filter(User.email == email).one_or_none()
    if not user or not user.is_active:
        return PlainTextResponse("Неверный код", status_code=400)

    try:
        ok = verify_code(user.id, "reset", code, db=db)
        if not ok:
            return PlainTextResponse("Неверный код", status_code=400)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=400)

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
    token = (token or "").strip()
    now = utcnow()

    rs = db.query(PasswordResetSession).filter(PasswordResetSession.id == token).one_or_none()
    if not rs or rs.is_used or rs.expires_at <= now:
        # либо отдельный шаблон ошибки, либо тот же с error
        return templates.TemplateResponse(
            "forgot_new_password.html",
            {"request": request, "token": None, "error": "Ссылка устарела, запросите новый код"},
        )

    return templates.TemplateResponse("forgot_new_password.html", {"request": request, "token": token, "error": None})


@app.post("/forgot/new-password")
async def forgot_set_new_password(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    token = (data.get("token") or "").strip()
    password = (data.get("password") or "")
    password_confirm = (data.get("password_confirm") or "")

    if password != password_confirm:
        return PlainTextResponse("Пароли не совпадают", status_code=400)

    err = validate_password(password)
    if err:
        return PlainTextResponse(err, status_code=400)

    now = utcnow()
    rs = db.query(PasswordResetSession).filter(PasswordResetSession.id == token).one_or_none()
    if not rs or rs.is_used or rs.expires_at <= now:
        return PlainTextResponse("Ссылка устарела, запросите новый код", status_code=400)

    user = db.query(User).filter(User.id == rs.user_id).one_or_none()
    if not user:
        return PlainTextResponse("Пользователь не найден", status_code=400)

    user.password_hash = hash_password(password)
    rs.is_used = True

    # инвалидируем все обычные сессии пользователя
    db.query(SessionModel).filter(SessionModel.user_id == user.id).delete(synchronize_session=False)

    db.commit()
    return JSONResponse({"status": "ok", "redirect": "/"}, status_code=200)
