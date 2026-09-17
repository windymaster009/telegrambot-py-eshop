from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    print("Create Telegram API credentials first at https://my.telegram.org")
    api_id_text = os.getenv("PAYMENT_READER_API_ID") or input("API ID: ").strip()
    api_hash = os.getenv("PAYMENT_READER_API_HASH") or getpass.getpass("API hash: ")
    phone = input("Dedicated reader account phone number (with country code): ").strip()

    try:
        api_id = int(api_id_text)
    except ValueError as exc:
        raise SystemExit("API ID must be a number") from exc
    if not api_hash or not phone:
        raise SystemExit("API hash and phone number are required")

    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        await client.start(phone=phone)
        session = client.session.save()
        me = await client.get_me()
        print(
            "\nSession created for "
            f"{getattr(me, 'username', None) or getattr(me, 'first_name', 'reader')}."
        )
        print("Keep these values private and add them to .env:\n")
        print(f"PAYMENT_READER_API_ID={api_id}")
        print(f"PAYMENT_READER_API_HASH={api_hash}")
        print(f"PAYMENT_READER_SESSION={session}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
