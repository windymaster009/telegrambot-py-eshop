# Windy Telegram E-Shop

A Telegram shop bot with a shared FastAPI backend and MongoDB Atlas database. Customers can browse
products, pay by Cambodia bank QR or wallet balance, submit payment proof, and receive account/key
stock automatically after approval. The REST API is ready for a separate admin website.

## Architecture

```text
Telegram bot ─────┐
                  ├── Shop service ── MongoDB Atlas
Admin website ─ API
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

## 3. Run both with PM2 on Raspberry Pi

Do not also run the systemd services or a Windows copy of the bot. Telegram long polling supports
only one active bot process.

```bash
cd /home/kevin/telegrambot-py-eshop
pm2 delete telegram-shop telegram-shop-bot telegram-shop-api 2>/dev/null || true
pm2 start ecosystem.config.cjs
pm2 save
pm2 status
pm2 logs telegram-shop-bot --lines 100
pm2 logs telegram-shop-api --lines 100
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
