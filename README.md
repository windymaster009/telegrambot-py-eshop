# Windy Telegram E-Shop

A Telegram shop bot with a shared FastAPI backend and MongoDB Atlas database. Customers can browse
products, pay by Cambodia bank QR or wallet balance, submit payment proof, and receive account/key
stock automatically after approval. The REST API is ready for a separate admin website.

## Architecture

```text
Customer bot ───────┐
ABA listener bot ───┼── Shop service ── MongoDB Atlas
Admin website ── API┘
```

The admin website must call the FastAPI backend. It must never receive `MONGO_URI` or connect to
MongoDB directly. The bot and API share the same service rules, so stock, orders, deposits, and
balances stay consistent.

## Features

- Product list with live price and available stock
- Product details and in-message checkout navigation
- MongoDB transaction-based stock reservation
- Cambodia QR payment proof with admin approval/rejection
- Wallet deposits and balance purchases
- Automatic ABA top-up matching with exact unique amounts and duplicate protection
- Automatic delivery of account/key/code stock
- English and Khmer customer interface
- Telegram admin panel
- Protected REST admin API for a future website
- Expired unpaid orders automatically release reserved stock
- Integer-cent prices to avoid floating-point money errors

## Security first

If a bot token, MongoDB URI, or API key is posted in chat or a screenshot, rotate it immediately.
Never commit `.env` or stock credentials. The branded payment poster is customer-facing and is
included intentionally so every deployment uses the same QR.

Use MongoDB Atlas **Database Access** to create a dedicated application user. Add only the server's
required address under **Network Access**. Do not put the URI in frontend JavaScript.

## 1. Install

Python 3.11 or newer is required.

```bash
git clone https://github.com/windymaster009/telegrambot-py-eshop.git
cd telegrambot-py-eshop
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Generate a private API key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env`:

```dotenv
BOT_TOKEN=your_new_private_bot_token
ADMIN_IDS=[123456789]
SUPPORT_USERNAME=@your_support_username
SHOP_NAME=Windy Shop

MONGO_URI=mongodb+srv://username:password@your-cluster.mongodb.net/?retryWrites=true&w=majority
MONGO_DB_NAME=eshop
API_KEY=paste_the_generated_random_key_here

PAYMENT_QR_PATH=assets/payment_qr.png
PAYMENT_ACCOUNT_NAME=YOUR ABA ACCOUNT NAME
PAYMENT_ACCOUNT_NUMBER=YOUR ACCOUNT NUMBER
PAYMENT_EXPIRY_MINUTES=30
TOPUP_EXPIRY_MINUTES=15

AUTO_TOPUP_ENABLED=false
PAYMENT_CHECK_BOT_TOKEN=
ABA_PAYMENT_GROUP_ID=
ABA_PAYMENT_BOT_USERNAME=PayWayByABA_bot
PAYMENT_READER_API_ID=
PAYMENT_READER_API_HASH=
PAYMENT_READER_SESSION=
```

Both `ADMIN_IDS=123,456` and `ADMIN_IDS=[123,456]` work. The bundled poster at
`assets/payment_qr.png` is sent for both product payments and wallet deposits.

This MongoDB version starts with a fresh Atlas database. The old SQLite `shop.db` is not read by the
application.

## 2. Test each process

Bot:

```bash
source .venv/bin/activate
python main.py
```

API, in a second terminal:

```bash
source .venv/bin/activate
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

Check it:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/products
```

Interactive API documentation is at `http://127.0.0.1:8000/docs`. Admin endpoints require an
`X-API-Key` header.

## 3. Run with PM2 on Raspberry Pi

Do not also run the systemd services or a Windows copy of the bot. Telegram long polling supports
only one active bot process.

```bash
cd /home/kevin/telegrambot-py-eshop
pm2 delete telegram-shop telegram-shop-bot telegram-shop-api telegram-payment-listener 2>/dev/null || true
pm2 start ecosystem.config.cjs
pm2 save
pm2 status
pm2 logs telegram-shop-bot --lines 100
pm2 logs telegram-shop-api --lines 100
pm2 logs telegram-payment-listener --lines 100
```

For reboot persistence:

```bash
pm2 startup
```

Run the exact `sudo ...` command printed by PM2, then run `pm2 save` again.

## API routes

Public:

- `GET /health`
- `GET /api/v1/products`
- `GET /api/v1/products/{id}`

Protected with `X-API-Key`:

- `GET/POST /api/v1/admin/products`
- `PUT /api/v1/admin/products/{id}`
- `POST /api/v1/admin/products/{id}/toggle`
- `POST /api/v1/admin/products/{id}/stock`
- `GET /api/v1/admin/orders`
- `POST /api/v1/admin/orders/{id}/approve`
- `POST /api/v1/admin/orders/{id}/reject`
- `GET /api/v1/admin/deposits`
- `POST /api/v1/admin/deposits/{id}/approve`
- `POST /api/v1/admin/deposits/{id}/reject`
- `GET /api/v1/admin/users`
- `GET /api/v1/admin/telegram-files/{file_id}`

Keep `API_KEY` in the admin website's server-side backend. Do not embed it in a public React/Vite
bundle. A Next.js server route, reverse proxy, or Cloudflare Access layer can call this API safely.

## Automatic ABA wallet top-ups

This first automation applies only to wallet deposits. Product QR orders still use the existing
payment-proof and admin-review flow.

The shop reserves the lowest available unique payable amount for each top-up queue. For example, a
requested `$10.00` top-up asks the first customer to pay `$10.01`; another simultaneous `$10.00`
queue uses `$10.02`. The ABA listener maps that exact amount to the queue and Telegram user, stores
the ABA transaction ID once, approves the deposit, and credits the exact amount paid. Repeated ABA
notifications cannot credit the wallet twice. A successfully confirmed amount is released
immediately, so the next queue can reuse `$10.01` instead of the cents continually increasing.

Each Telegram customer can have only one active top-up queue at a time. Unpaid queues become
`expired` after `TOPUP_EXPIRY_MINUTES` (15 minutes by default). Cancelled and expired amounts stay
quarantined for one additional expiry period to stop a late bank notification from matching a new
queue. Customers can use **Cancel top-up** only before paying. A slot under manual proof review uses
a 24-hour fail-safe reservation, or is released earlier when an administrator approves or rejects
it.

1. Create a new, dedicated check bot with BotFather. It sends confirmations and handles
   `/chatid` and `/listenerstatus`.
2. Telegram's Bot API does not deliver messages authored by another bot. Create a dedicated
   Telegram **user account** for read-only payment monitoring and add that account to the private
   ABA notification group. Do not reuse a personal account containing sensitive chats.
3. Create an API ID and API hash for that reader account at
   [my.telegram.org](https://my.telegram.org), install the updated dependencies, and generate its
   encrypted session string:

   ```bash
   cd /home/kevin/telegrambot-py-eshop
   source .venv/bin/activate
   pip install -r requirements.txt
   python scripts/create_payment_reader_session.py
   ```

   Telegram will ask for the reader account phone number, login code, and two-step password when
   enabled. Copy the three printed `PAYMENT_READER_*` lines into `.env`. The session string gives
   access to that Telegram account; never paste it into chat or commit it to Git.
4. Configure the listener:

   ```dotenv
   AUTO_TOPUP_ENABLED=true
   PAYMENT_CHECK_BOT_TOKEN=your_dedicated_check_bot_token
   ABA_PAYMENT_GROUP_ID=-1005407734841
   TOPUP_EXPIRY_MINUTES=15
   PAYMENT_READER_API_ID=your_api_id
   PAYMENT_READER_API_HASH=your_api_hash
   PAYMENT_READER_SESSION=your_private_session_string
   ```

5. Restart the listener and inspect startup:

   ```bash
   pm2 restart telegram-payment-listener --update-env
   pm2 logs telegram-payment-listener --lines 100
   ```

   A healthy startup logs `Telegram user-account payment reader connected`.
6. In the ABA group, send `/listenerstatus@YourCheckBotUsername`. It should show
   **PayWay bot-message reader: ready**.
7. Make a small real payment for the exact amount shown by the shop. A received payment logs
   `Scanned ABA transaction ...`, credits the wallet once, and sends the customer a
   **Payment successful** message. If no matching payment is received within 15 minutes, the
   customer receives a **Payment failed / timed out** message.

The user-account reader scans every text received in the configured payment group. A matching ABA
message records the amount, payer name/account suffix, displayed payment time, transaction ID, APV,
payment channel and merchant. Transaction IDs are stored once, so replaying the same receipt cannot
credit a wallet twice. Because the sender is not checked, keep this group private and allow only ABA
and trusted administrators; any member who can post could otherwise submit forged ABA-looking text.
Keep the manual **Submit payment proof** button as the fallback if a notification is delayed or its
format changes.

## Telegram admin workflow

1. Send `/admin` in a private chat.
2. Select **Add product** and send:

   ```text
   Product name | 12.00 | 30 days | Product description
   ```

3. Open **Products**, select the product, and choose **Add stock**.
4. Send one account, key, or code per line.
5. Verify that money arrived before approving any payment screenshot.

## Tests

```bash
python -m pip install -r requirements-dev.txt
ruff check .
pytest -q
```

The tests use an in-memory MongoDB-compatible test double. Production purchases use MongoDB Atlas
multi-document transactions to protect balance deductions and inventory reservation.
