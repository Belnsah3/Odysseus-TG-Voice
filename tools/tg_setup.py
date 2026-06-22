#!/usr/bin/env python3
"""Telegram userbot setup: login, list groups, export group snapshot.

Сервер в РФ → Telegram только через прокси (SOCKS5/HTTP из .env).
Код входа приходит на **телефон** (чат «Telegram» в приложении), не на сервер.

Usage:
  cd ~/projects/odysseus-voice && source .venv/bin/activate
  set -a && source .env && set +a

  # Проверить, что прокси достаёт до Telegram:
  python tools/tg_setup.py check

  # Вход (прокси по умолчанию, сбросить битую сессию):
  python tools/tg_setup.py login --reset-session

  # Если код не в приложении — форсировать SMS:
  python tools/tg_setup.py login --reset-session --sms

  # Если SOCKS5 глючит — HTTP-прокси:
  python tools/tg_setup.py login --proxy http --reset-session

  # Вход по QR (без SMS/кода) — лучший вариант:
  python tools/tg_setup.py login-qr --proxy none --reset-session

  # Перенос сессии с другого сервера:
  python tools/tg_setup.py export-string          # там, где залогинились
  python tools/tg_setup.py import-string "1BVts…" # здесь, на РФ-сервере

Non-interactive:
  TG_PHONE=+79360002235 TG_CODE=12345 python tools/tg_setup.py login
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "voice_bridge"))

# Sessions path must be set before importing voice_bridge.config
os.environ.setdefault("SESSIONS_DIR", os.path.join(ROOT, "voice_bridge", "sessions"))

_env = os.path.join(ROOT, ".env")
if os.path.isfile(_env):
    with open(_env, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import config  # noqa: E402
from telethon import TelegramClient  # noqa: E402
from tg_group_info import list_groups, save_snapshot, snapshot_group  # noqa: E402
from tg_client import build_client  # noqa: E402


def _sessions_dir() -> str:
    os.makedirs(config.SESSIONS_DIR, exist_ok=True)
    return config.SESSIONS_DIR


def _session_path() -> str:
    return os.path.join(_sessions_dir(), config.TG_SESSION_NAME)


def _reset_session_files() -> None:
    base = _session_path()
    for p in (base + ".session", base + ".session-journal"):
        if os.path.isfile(p):
            os.remove(p)
            print(f"Removed: {p}")


def _proxy_kind_from_args(args) -> str | None:
    kind = getattr(args, "proxy", None)
    if kind == "none":
        return None
    return kind or config.TG_PROXY_KIND


def _describe_sent_code(sent) -> str:
    stype = type(sent.type).__name__
    hints = {
        "SentCodeTypeApp": (
            "Код отправлен в приложение Telegram на телефоне с этим номером.\n"
            "Открой Telegram → чат «Telegram» / «Код для входа».\n"
            "Это не SMS и не сообщение на сервер — смотри телефон."
        ),
        "SentCodeTypeSms": "Код отправлен SMS на этот номер.",
        "SentCodeTypeCall": "Код продиктуют звонком на этот номер.",
        "SentCodeTypeFragmentSms": "Код через Fragment SMS.",
        "SentCodeTypeFirebaseSms": "Код через Firebase SMS.",
        "SentCodeTypeEmailCode": "Код отправлен на привязанный email.",
    }
    return hints.get(stype, f"Тип доставки: {stype}")


async def _check_proxy(proxy_kind: str | None) -> None:
    """Verify MTProto can reach Telegram through the configured proxy."""
    url = config.proxy_url_for(proxy_kind) if proxy_kind else ""
    if not url and proxy_kind is not None:
        raise SystemExit("No proxy URL in .env (PROXY_URL / PROXY_URL_HTTP)")

    kinds_to_try: list[str | None] = []
    if proxy_kind:
        kinds_to_try = [proxy_kind]
    else:
        kinds_to_try = ["socks5", "http"]

    print("Проверка доступа к Telegram…\n")
    for kind in kinds_to_try:
        u = config.proxy_url_for(kind)
        if not u:
            print(f"  [{kind}] пропуск — URL не задан")
            continue
        print(f"  [{kind}] {u.split('@')[-1] if '@' in u else u} …", end=" ", flush=True)
        client = build_client(use_proxy=True, proxy_kind=kind)
        try:
            await asyncio.wait_for(client.connect(), timeout=25)
            ok = client.is_connected()
            auth = await client.is_user_authorized() if ok else False
            print("OK" + (" (уже авторизован)" if auth else " (не залогинен)"))
        except Exception as e:
            print(f"FAIL: {e}")
        finally:
            if client.is_connected():
                await client.disconnect()

    print(
        "\nЕсли оба FAIL — прокси не пускает MTProto. Если OK — login должен работать.\n"
        "Код входа всё равно приходит на телефон, даже когда сервер в РФ."
    )


async def _login(client, *, force_sms: bool = False) -> None:
    phone = os.environ.get("TG_PHONE", "").strip()
    code = os.environ.get("TG_CODE", "").strip()
    password = os.environ.get("TG_PASSWORD", "").strip()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Already logged in as {me.first_name} (@{me.username}) id={me.id}")
        return

    if not phone:
        phone = input("Phone (international, e.g. +79360002235): ").strip()
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("+")

    print(f"\nЗапрашиваю код для {phone} через прокси …")
    print("Параллельно открой Telegram на телефоне — чат «Telegram».\n")
    try:
        sent = await client.send_code_request(phone, force_sms=force_sms)
    except Exception as e:
        err = str(e)
        if "FLOOD" in err.upper() or "wait" in err.lower():
            print(
                "\nTelegram временно заблокировал вход (3 неверных кода ранее).\n"
                "Подожди 15–60 минут, затем:\n"
                "  python tools/tg_setup.py login --reset-session\n"
                f"\nОшибка: {e}"
            )
        raise SystemExit(1) from e

    print(_describe_sent_code(sent))
    print()

    if not code:
        code = input("Введи код с телефона (только цифры): ").strip()

    try:
        await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
    except Exception as e:
        err = str(e)
        if "Two-steps verification" in err or "password" in err.lower():
            if not password:
                password = input("2FA password: ").strip()
            await client.sign_in(password=password)
        elif "PHONE_CODE_INVALID" in err or "Invalid code" in err:
            print(
                "\nНеверный код. Проверь:\n"
                "  1) Чат «Telegram» в приложении на телефоне (не SMS)\n"
                "  2) Только цифры, без пробелов\n"
                "  3) Код ~3 мин — не жми Enter пустым\n"
                "  4) Новый код: python tools/tg_setup.py login --reset-session --sms\n"
            )
            raise SystemExit(1) from e
        else:
            raise

    me = await client.get_me()
    print(f"\nLogged in: {me.first_name} (@{me.username}) id={me.id}")
    print(f"Session: {_session_path()}.session")


def _print_qr(url: str) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        pass
    print(f"\nСсылка для телефона:\n{url}\n")


async def _login_qr(client) -> None:
    from telethon.errors import SessionPasswordNeededError

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Already logged in as {me.first_name} (@{me.username}) id={me.id}")
        return

    password = os.environ.get("TG_PASSWORD", "").strip()
    print("\n=== QR-вход ===")
    print("1. Telegram → Настройки → Устройства → Подключить устройство")
    print("2. Сканируй QR сразу (живёт ~30 сек, обновляется автоматически)")
    print("3. Если включена 2FA — введи облачный пароль НА ТЕЛЕФОНЕ при запросе")
    print("   Или заранее: export TG_PASSWORD='твой_пароль'\n")

    for attempt in range(1, 13):  # up to ~6 min of refreshed QRs
        qr = await client.qr_login()
        print(f"--- QR #{attempt} (до {qr.expires.strftime('%H:%M:%S')} UTC) ---")
        _print_qr(qr.url)

        try:
            ttl = min(28, max(5, (qr.expires - __import__("datetime").datetime.now(
                tz=__import__("datetime").timezone.utc)).total_seconds() - 2))
            await qr.wait(timeout=ttl)
            break
        except SessionPasswordNeededError:
            if not password:
                password = input("\n2FA облачный пароль (с сервера): ").strip()
            await client.sign_in(password=password)
            break
        except asyncio.TimeoutError:
            print("QR истёк — генерирую новый…\n")
            continue
    else:
        raise SystemExit("Не удалось войти за 6 минут. Запусти login-qr снова.")

    me = await client.get_me()
    print(f"\nLogged in: {me.first_name} (@{me.username}) id={me.id}")
    print(f"Session: {_session_path()}.session")
    print("\nЭкспорт для РФ-сервера:")
    print("  python tools/tg_setup.py export-string")


async def _export_string(client) -> None:
    from telethon.sessions import StringSession

    if not await client.is_user_authorized():
        raise SystemExit("Not logged in")
    s = StringSession.save(client.session)
    me = await client.get_me()
    print(f"# {me.first_name} (@{me.username}) id={me.id}")
    print(s)
    print("\nНа РФ-сервере: python tools/tg_setup.py import-string \"<строка выше>\"")


async def _import_string(
    session_string: str, *, use_proxy: bool, proxy_kind: str | None,
) -> None:
    from telethon.sessions import SQLiteSession, StringSession

    session_string = session_string.strip()
    if not session_string:
        raise SystemExit("Empty session string")

    proxy = config.parse_proxy(config.proxy_url_for(proxy_kind)) if use_proxy else None
    sc = TelegramClient(
        StringSession(session_string),
        config.TG_API_ID,
        config.TG_API_HASH,
        proxy=proxy,
    )
    await sc.connect()
    if not await sc.is_user_authorized():
        await sc.disconnect()
        raise SystemExit("Invalid or expired session string")

    me = await sc.get_me()
    path = _session_path()
    sqlite = SQLiteSession(path)
    sqlite.set_dc(sc.session.dc_id, sc.session.server_address, sc.session.port)
    sqlite.auth_key = sc.session.auth_key
    sqlite.save()
    await sc.disconnect()

    # Verify file session
    vc = build_client(use_proxy=use_proxy, proxy_kind=proxy_kind)
    await vc.connect()
    if not await vc.is_user_authorized():
        await vc.disconnect()
        raise SystemExit("Import failed — use TG_SESSION_STRING in .env instead")
    await vc.disconnect()

    print(f"Imported session for {me.first_name} (@{me.username}) id={me.id}")
    print(f"Saved: {path}.session")


async def _groups(client, limit: int) -> None:
    rows = await list_groups(client, limit=limit)
    if not rows:
        print("No groups found.")
        return
    print(f"{'chat_id':<16} {'call':<6} {'parts':<6} title")
    print("-" * 60)
    for r in rows:
        call = "YES" if r["active_call"] else "no"
        parts = r["call_participants"] or "-"
        print(f"{r['chat_id']:<16} {call:<6} {str(parts):<6} {r['title']}")
    print(f"\nTotal: {len(rows)} groups/supergroups")


async def _export(client, chat_id: int | None, title: str | None) -> None:
    if chat_id is None and title:
        async for dialog in client.iter_dialogs():
            if (dialog.title or "").lower() == title.lower():
                chat_id = dialog.id
                break
        if chat_id is None:
            raise SystemExit(f"Group not found by title: {title!r}")
    if chat_id is None:
        raise SystemExit("Provide --chat-id or --title")

    print(f"Fetching group {chat_id} …")
    snap = await snapshot_group(client, chat_id)
    out = os.path.join(_sessions_dir(), f"group_{chat_id}.json")
    save_snapshot(out, snap)
    print(f"Saved: {out}")
    print(json.dumps({
        "chat_id": snap.chat_id,
        "title": snap.title,
        "members": snap.members_count,
        "active_call": snap.group_call.active,
        "call_participants": snap.group_call.participants_count,
        "exported_members": len(snap.members),
    }, ensure_ascii=False, indent=2))


def _add_proxy_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--proxy",
        choices=("socks5", "http", "none"),
        default=None,
        help="socks5 (default) | http | none — only 'none' for tests outside RF",
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram userbot setup (proxy required in RF)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Test Telegram reachability via proxy")
    _add_proxy_arg(p_check)

    p_login = sub.add_parser("login", help="Sign in and save .session file")
    _add_proxy_arg(p_login)
    p_login.add_argument("--reset-session", action="store_true", help="Delete broken .session")
    p_login.add_argument("--sms", action="store_true", help="Force SMS code")

    p_qr = sub.add_parser("login-qr", help="Sign in by scanning QR (no SMS code)")
    _add_proxy_arg(p_qr)
    p_qr.add_argument("--reset-session", action="store_true")

    p_es = sub.add_parser("export-string", help="Print StringSession for transfer")
    _add_proxy_arg(p_es)

    p_is = sub.add_parser("import-string", help="Import StringSession → .session file")
    p_is.add_argument("session_string", help="StringSession from export-string")
    _add_proxy_arg(p_is)

    g = sub.add_parser("groups", help="List groups with active call flag")
    g.add_argument("--limit", type=int, default=100)
    _add_proxy_arg(g)

    e = sub.add_parser("export", help="Export group members + call info to JSON")
    e.add_argument("--chat-id", type=int, default=None)
    e.add_argument("--title", type=str, default=None)
    _add_proxy_arg(e)

    args = parser.parse_args()

    if getattr(args, "reset_session", False):
        _reset_session_files()

    proxy_kind = _proxy_kind_from_args(args)
    use_proxy = proxy_kind != "none" and args.cmd != "check" or (
        args.cmd == "check" and proxy_kind != "none"
    )
    if args.cmd == "check":
        await _check_proxy(None if args.proxy is None else proxy_kind)
        return

    if args.cmd == "import-string":
        use_proxy = getattr(args, "proxy", None) != "none"
        if args.proxy is None:
            use_proxy = config.TG_USE_PROXY
        await _import_string(
            args.session_string,
            use_proxy=use_proxy,
            proxy_kind=proxy_kind if proxy_kind else config.TG_PROXY_KIND,
        )
        return

    client = build_client(
        use_proxy=proxy_kind != "none",
        proxy_kind=proxy_kind if proxy_kind != "none" else None,
    )
    await client.connect()

    try:
        if args.cmd == "login":
            await _login(client, force_sms=getattr(args, "sms", False))
        elif args.cmd == "login-qr":
            await _login_qr(client)
        elif args.cmd == "export-string":
            await _export_string(client)
        else:
            if not await client.is_user_authorized():
                raise SystemExit(
                    "Not logged in. Run:\n"
                    "  python tools/tg_setup.py check\n"
                    "  python tools/tg_setup.py login --reset-session"
                )
            if args.cmd == "groups":
                await _groups(client, args.limit)
            elif args.cmd == "export":
                await _export(client, args.chat_id, args.title)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
