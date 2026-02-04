# -*- coding: utf-8 -*-
"""Small dictionary of user-facing error and message strings by lang (ru/en)."""

MESSAGES = {
    "en": {
        "email_required": "Email is required",
        "password_empty": "Password must not be empty",
        "password_min_length": "Password must be at least 8 characters.",
        "password_no_spaces": "Password must not contain spaces.",
        "password_digit": "Password must contain at least one digit.",
        "password_lower": "Password must contain at least one lowercase letter.",
        "password_upper": "Password must contain at least one uppercase letter.",
        "password_special": "Password must contain at least one special character.",
        "email_taken": "Email is already taken",
        "send_email_failed": "Failed to send email",
        "user_not_found": "User not found",
        "registration_failed": "Failed to complete registration",
        "invalid_code": "Invalid code",
        "code_cooldown": "You can request a code no more than once per minute",
        "email_not_verified": "Email not verified. Complete registration.",
        "code_sent_if_exists": "If this email exists, a code has been sent",
        "link_expired": "Link expired. Please request a new code.",
        "passwords_do_not_match": "Passwords do not match",
        "incorrect_email_or_password": "Incorrect email or password",
        "code_used": "Code already used",
        "code_expired": "Code expired",
        "too_many_attempts": "Too many attempts",
    },
    "ru": {
        "email_required": "Email обязателен",
        "password_empty": "Пароль не должен быть пустым",
        "password_min_length": "Пароль должен содержать не менее 8 символов.",
        "password_no_spaces": "Пароль не должен содержать пробелы.",
        "password_digit": "Пароль должен содержать минимум одну цифру.",
        "password_lower": "Пароль должен содержать минимум одну строчную букву.",
        "password_upper": "Пароль должен содержать минимум одну заглавную букву.",
        "password_special": "Пароль должен содержать минимум один спецсимвол.",
        "email_taken": "Email уже занят",
        "send_email_failed": "Не удалось отправить письмо",
        "user_not_found": "Пользователь не найден",
        "registration_failed": "Не удалось завершить регистрацию",
        "invalid_code": "Неверный код",
        "code_cooldown": "Код можно запрашивать не чаще 1 раза в минуту",
        "email_not_verified": "Email не подтверждён. Завершите регистрацию.",
        "code_sent_if_exists": "Если такой email существует, код отправлен",
        "link_expired": "Ссылка устарела, запросите новый код",
        "passwords_do_not_match": "Пароли не совпадают",
        "incorrect_email_or_password": "Неверный email или пароль",
        "code_used": "Код уже использован",
        "code_expired": "Код истёк",
        "too_many_attempts": "Слишком много попыток",
    },
}

SUPPORTED = {"en", "ru"}
DEFAULT = "ru"


def t(lang: str | None, key: str) -> str:
    """Return message for key in given lang; fallback to default lang."""
    lang = (lang or "").strip().lower() if lang else DEFAULT
    if lang not in SUPPORTED:
        lang = DEFAULT
    return MESSAGES.get(lang, MESSAGES[DEFAULT]).get(key, MESSAGES[DEFAULT].get(key, key))
