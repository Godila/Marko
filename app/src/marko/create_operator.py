"""Сеанс заведения/смены пароля оператора консоли МАРКО.

Запуск на проде (пароль вводится в терминал, не в argv/логи/чат):
    docker compose exec -T api python -m marko.create_operator

Смена пароля = повторный запуск (активные сессии не инвалидируются —
сознательно; при необходимости: DELETE FROM platform.sessions).
"""
import getpass
import sys

from marko.platform import auth
from marko.db import SessionLocal

MIN_LEN = 10


def _ask_password() -> str:
    print("Пароль оператора (не менее %d символов):" % MIN_LEN)
    pw = getpass.getpass("  пароль: ")
    if not sys.stdin.isatty():                     # docker exec -i без TTY
        pw = pw.strip() or sys.stdin.readline().rstrip("\n")
    if len(pw) < MIN_LEN:
        sys.exit(f"пароль короче {MIN_LEN} символов — повторите запуск")
    pw2 = getpass.getpass("  ещё раз: ") if sys.stdin.isatty() \
        else sys.stdin.readline().rstrip("\n")
    if pw != pw2:
        sys.exit("пароли не совпали — повторите запуск")
    return pw


def main() -> None:
    default = "operator"
    username = input(f"Логин [{default}]: ").strip() or default
    password = _ask_password()
    db = SessionLocal()
    try:
        auth.ensure_user(db, username, password)
    finally:
        db.close()
    print(f"ok: user '{username}' создан/обновлён (пароль нигде не печатается)")


if __name__ == "__main__":
    main()
