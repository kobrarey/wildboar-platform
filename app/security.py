import re

import bcrypt

from app.i18n import t


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
