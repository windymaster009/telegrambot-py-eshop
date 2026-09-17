# Telegram Python E-Shop

A Telegram shop bot built with Python, aiogram 3, SQLAlchemy, and SQLite. Customers can browse
products, choose a quantity, pay by Cambodia bank QR or wallet balance, submit payment proof, and
receive purchased account/key stock automatically after admin approval.

## Features

- Product list with live price and available stock
- In-message product detail and checkout navigation
- Safe inventory reservation while QR payment is pending
- Cambodia QR payment proof with admin approve/reject buttons
- Wallet deposits and balance purchases
- Automatic delivery of account/key/code stock
- Order history
- English and Khmer customer interface
- Telegram admin panel for products, stock, orders, and deposits
- Expired unpaid orders release their reserved stock automatically
- Prices stored as integer cents to avoid floating-point money errors

## 1. Create the bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot`, choose a name and username, then copy the bot token.
3. Find your numeric Telegram ID using a bot such as [@userinfobot](https://t.me/userinfobot).

Never post the bot token in GitHub, screenshots, or chat groups. If a token is exposed, revoke it
with BotFather immediately.

## 2. Install

Python 3.11 or newer is required.

```bash
git clone https://github.com/windymaster009/telegrambot-py-eshop.git
cd telegrambot-py-eshop
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```dotenv
BOT_TOKEN=your_real_bot_token
ADMIN_IDS=your_numeric_telegram_id
SUPPORT_USERNAME=@your_support_username
SHOP_NAME=Your Shop Name
DATABASE_URL=sqlite+aiosqlite:///shop.db
PAYMENT_QR_PATH=assets/payment_qr.png
PAYMENT_ACCOUNT_NAME=YOUR ABA ACCOUNT NAME
PAYMENT_ACCOUNT_NUMBER=YOUR ACCOUNT NUMBER
PAYMENT_EXPIRY_MINUTES=30
```

Multiple admins are supported: `ADMIN_IDS=123456789,987654321`.

Put the real QR image at `assets/payment_qr.png`. The QR and `.env` are ignored by Git.

## 3. Run

```bash
source .venv/bin/activate
python main.py
```

Open the bot in Telegram and send `/start`. An admin can send `/admin` in a private chat.

## Admin workflow

1. Send `/admin`.
2. Select **Add product** and send:

   ```text
   Product name | 12.00 | 30 days | Product description
   ```

   Optional Khmer product text is supported:

   ```text
   Name EN | 12.00 | 30 days | Description EN | Name KM | Description KM
   ```

3. Open **Products**, choose the product, then select **Add stock**.
4. Send one account, key, or code per line. Each line counts as one stock item.
5. When a customer uploads payment proof, admins receive the screenshot with **Approve & deliver**
   and **Reject** buttons.

Use **Edit product** to change its name, price, warranty, description, or Khmer text. Use
**Disable** to hide a product without deleting its order history or stock.

Example stock message:

```text
email1@example.com | password1
email2@example.com | password2
license-key-0003
```

Only add stock in a private chat with the bot. Stock content is stored in the local database and is
never committed to Git.

## Run on Raspberry Pi with systemd

After installing the project at `/home/kevin/telegrambot-py-eshop` and testing `python main.py`:

```bash
sudo cp deploy/telegram-shop.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-shop
sudo systemctl status telegram-shop
```

Live logs:

```bash
journalctl -u telegram-shop -f
```

If the Linux username or project path is different, edit `deploy/telegram-shop.service` first.

## Tests

```bash
pip install pytest pytest-asyncio ruff
ruff check .
pytest -q
```

## Production notes

- Back up `shop.db` regularly. It contains users, balances, orders, and unsold stock.
- Limit server access because stock credentials are sensitive.
- Manual screenshot approval does not prove a bank payment by itself. The admin must verify the
  money arrived before pressing approve.
- SQLite is suitable for a small shop. Move `DATABASE_URL` to PostgreSQL before running multiple bot
  processes or handling high order volume.
- Run only one instance when using SQLite and the in-memory Telegram conversation state.
