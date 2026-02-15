"""Session and get_current_user dependencies."""
import uuid
from datetime import datetime, timedelta, timezone

from starlette.requests import Request
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, SessionModel

SESSION_TTL_DAYS = settings.SESSION_TTL_DAYS
COOKIE_NAME = settings.COOKIE_NAME


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NotAuthenticated(Exception):
    pass


def create_session(db: Session, user_id: int, commit: bool = True) -> str:
    db.query(SessionModel).filter(SessionModel.expires_at < utcnow()).delete(synchronize_session=False)

    session_id = uuid.uuid4().hex
    expires_at = utcnow() + timedelta(days=SESSION_TTL_DAYS)

    db.add(SessionModel(id=session_id, user_id=user_id, created_at=utcnow(), expires_at=expires_at))

    if commit:
        db.commit()
    else:
        db.flush()

    return session_id


def get_current_user(request: Request, db: Session) -> User:
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
