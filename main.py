import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy import create_engine, String, BigInteger, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, declarative_base, Mapped, mapped_column
from sqlalchemy.exc import IntegrityError
from typing import Generator
import bcrypt


# -----------------------
# DB config
# -----------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:пароль@localhost:5432/WildBoar_platform"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


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


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_session(db: Session, user_id: int) -> str:
    session_id = uuid.uuid4().hex  # 32-символьная строка
    expires_at = utcnow() + timedelta(days=SESSION_TTL_DAYS)

    db_sess = SessionModel(
        id=session_id,
        user_id=user_id,
        created_at=utcnow(),
        expires_at=expires_at,
    )
    db.add(db_sess)
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
    email_norm = (email or "").strip().lower()
    pwd = (password or "").strip()

    if not pwd:
        return PlainTextResponse("Password must not be empty", status_code=400)

    existing = db.query(User).filter(User.email == email_norm).first()
    if existing:
        return PlainTextResponse("Email is already taken", status_code=400)

    password_hash = hash_password(pwd)

    user = User(
        created_at=utcnow(),
        email=email_norm,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=(phone.strip() if phone else None),
        password_hash=password_hash,
        is_active=True,
    )

    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return PlainTextResponse("Email is already taken", status_code=400)

    db.refresh(user)

    session_id = create_session(db, user.id)

    resp = RedirectResponse(url="/dashboard", status_code=302)
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
    if not user or not user.is_active:
        return PlainTextResponse("Invalid credentials", status_code=401)

    if not verify_password(pwd, user.password_hash):
        return PlainTextResponse("Invalid credentials", status_code=401)

    session_id = create_session(db, user.id)

    resp = RedirectResponse(url="/dashboard", status_code=302)
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
