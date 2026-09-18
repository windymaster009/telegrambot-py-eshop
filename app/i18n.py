from collections.abc import Mapping

from app.models import Language

TEXTS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "welcome": (
            "👋 Welcome to <b>{shop_name}</b>!\n\n"
            "👤 Username: {username}\n"
            "💰 Balance: <b>{balance}</b>\n\n"
            "🛍 Choose a product below:"
        ),
        "products": "🛍 <b>Our products</b>\nSelect a product to see details:",
        "no_products": "📭 No products are available right now. Please check again later.",
        "product": (
            "📦 <b>{name}</b>\n\n"
            "💵 Price: <b>{price}</b>\n"
            "🛡 Warranty: {warranty}\n"
            "📊 In stock: <b>{stock}</b>\n\n"
            "{description}"
        ),
        "order_summary": (
            "🧾 <b>{name}</b> × {quantity}\n💰 Total: <b>{total}</b>\n\nHow do you want to pay?"
        ),
        "out_of_stock": "❌ Sorry, there is not enough stock for that quantity.",
        "choose_quantity": "🔢 Send the quantity you want (1–{max_quantity}).",
        "invalid_quantity": "Please send a whole number between 1 and {max_quantity}.",
        "balance": "💰 Your current balance is: <b>{balance}</b>",
        "insufficient_balance": (
            "❌ Insufficient balance. You need {needed}, but your balance is {balance}."
        ),
        "purchase_complete": (
            "✅ <b>Payment successful!</b>\n"
            "Order #{order_id} — {name} × {quantity}\n\n"
            "🔐 <b>Your purchased item(s):</b>\n<code>{delivery}</code>\n\n"
            "Keep this message private."
        ),
        "payment_caption": (
            "🧾 <b>Order #{order_id}</b>\n"
            "📦 {name} × {quantity}\n"
            "💵 Pay exactly: <b>{total}</b>\n"
            "🏦 Account: <b>{account_name}</b> ({account_number})\n\n"
            "Scan the BLINK KHQR in this poster and pay the exact amount. "
            "Then send the payment screenshot here. "
            "This order expires in {minutes} minutes."
        ),
        "payment_no_qr": (
            "🧾 <b>Order #{order_id}</b>\n"
            "Pay exactly <b>{total}</b> to {account_name} ({account_number}).\n\n"
            "⚠️ The payment QR has not been configured yet. Please contact support."
        ),
        "send_proof": "📸 Now send your payment screenshot as a photo.",
        "proof_received": (
            "✅ Payment screenshot received for order #{order_id}.\n"
            "An admin will review it and deliver your item here."
        ),
        "proof_photo_only": "Please send the payment screenshot as a photo.",
        "order_expired": "⌛ This payment order expired. Please return to the shop and try again.",
        "orders_empty": "🧾 You haven't bought anything yet — open the Shop 🛍",
        "orders": "🧾 <b>My orders</b>\n\n{orders}",
        "order_line": "#{id} · {name} × {quantity}\n{total} · {status} · {date}",
        "deposit_prompt": (
            "➕ Send the amount you want to deposit in USD (example: <code>10</code>)."
        ),
        "deposit_amount_invalid": (
            "Send a valid amount, for example: <code>10</code> or <code>12.50</code>."
        ),
        "deposit_caption": (
            "➕ <b>Deposit #{deposit_id}</b>\n"
            "Pay exactly: <b>{amount}</b>\n"
            "🏦 Account: <b>{account_name}</b> ({account_number})\n\n"
            "Scan the BLINK KHQR in this poster. After paying, send the payment screenshot here."
        ),
        "deposit_auto_caption": (
            "➕ <b>Top-up queue #{deposit_id}</b>\n"
            "Requested: {requested_amount}\n"
            "💵 Pay exactly: <b>{amount}</b>\n"
            "🔖 Queue ID: <code>TOPUP-{deposit_id}</code>\n"
            "🏦 Account: <b>{account_name}</b> ({account_number})\n\n"
            "Scan the BLINK KHQR and enter the exact amount, including the cents. "
            "The extra cents are a temporary verification code, not a fee; the full amount paid "
            "is credited. The ABA notification will confirm this top-up automatically. This "
            "queue expires in {minutes} minutes. Only one queue can be active per account.\n\n"
            "If it is not confirmed after a few minutes, use the button below to send proof. "
            "Only cancel if you have not paid."
        ),
        "deposit_received": (
            "✅ Deposit screenshot received. An admin will review it soon.\n"
            "Your balance will update after approval."
        ),
        "deposit_approved": (
            "✅ <b>Payment successful!</b> Top-up #{deposit_id} is confirmed.\n"
            "Your new balance is {balance}."
        ),
        "deposit_rejected": (
            "❌ Deposit #{deposit_id} was rejected. Contact support if you need help."
        ),
        "submit_payment_proof": "📸 Submit payment proof",
        "cancel_topup": "❌ Cancel top-up",
        "topup_cancelled": "❌ Top-up #{deposit_id} cancelled. You can create a new one now.",
        "topup_already_cancelled": "This top-up was already cancelled.",
        "topup_expired": "⌛ This top-up expired. Please create a new one.",
        "topup_failed": (
            "❌ <b>Payment failed / timed out</b>\n"
            "No matching ABA payment was received for top-up #{deposit_id} within "
            "{minutes} minutes. Your balance was not changed. If you already paid, "
            "contact support."
        ),
        "topup_not_pending": "This top-up is no longer pending.",
        "topup_already_approved": "✅ This top-up has already been confirmed.",
        "order_rejected": (
            "❌ Payment for order #{order_id} was rejected. Reserved stock has been released."
        ),
        "contact": "📞 Need help? Contact our admin: {support}",
        "language": "🌐 Please choose your language:",
        "language_changed": "✅ Language changed to English.",
        "cancelled": "Cancelled.",
        "already_processed": "This request has already been processed.",
        "menu_shop": "🛍 Shop",
        "menu_balance": "💰 Balance",
        "menu_deposit": "➕ Deposit",
        "menu_contact": "📞 Contact",
        "menu_language": "🌐 Language",
        "menu_orders": "🧾 My Orders",
        "buy_one": "🛒 Buy 1 — {price}",
        "choose_qty": "🔢 Choose quantity (1–{stock})",
        "back": "⬅️ Back",
        "pay_now": "💳 Pay now — {total}",
        "pay_balance": "👛 Buy with balance — {total}",
        "status_awaiting_payment": "Waiting for payment",
        "status_awaiting_review": "Payment under review",
        "status_completed": "Completed",
        "status_rejected": "Rejected",
        "status_cancelled": "Cancelled",
    },
    "km": {
        "welcome": (
            "👋 សូមស្វាគមន៍មកកាន់ <b>{shop_name}</b>!\n\n"
            "👤 ឈ្មោះអ្នកប្រើ: {username}\n"
            "💰 សមតុល្យ: <b>{balance}</b>\n\n"
            "🛍 សូមជ្រើសរើសផលិតផលខាងក្រោម៖"
        ),
        "products": "🛍 <b>ផលិតផលរបស់យើង</b>\nជ្រើសរើសផលិតផលដើម្បីមើលព័ត៌មាន៖",
        "no_products": "📭 មិនមានផលិតផលនៅពេលនេះទេ។ សូមពិនិត្យម្ដងទៀតពេលក្រោយ។",
        "product": (
            "📦 <b>{name}</b>\n\n"
            "💵 តម្លៃ: <b>{price}</b>\n"
            "🛡 ការធានា: {warranty}\n"
            "📊 នៅសល់: <b>{stock}</b>\n\n"
            "{description}"
        ),
        "order_summary": (
            "🧾 <b>{name}</b> × {quantity}\n💰 សរុប: <b>{total}</b>\n\nតើអ្នកចង់បង់ប្រាក់តាមវិធីណា?"
        ),
        "out_of_stock": "❌ សូមអភ័យទោស ស្តុកមិនគ្រប់សម្រាប់ចំនួននេះទេ។",
        "choose_quantity": "🔢 សូមផ្ញើចំនួនដែលអ្នកចង់ទិញ (1–{max_quantity})។",
        "invalid_quantity": "សូមផ្ញើលេខគត់ចន្លោះពី 1 ដល់ {max_quantity}។",
        "balance": "💰 សមតុល្យបច្ចុប្បន្នរបស់អ្នកគឺ៖ <b>{balance}</b>",
        "insufficient_balance": "❌ សមតុល្យមិនគ្រប់។ ត្រូវការ {needed} ប៉ុន្តែអ្នកមាន {balance}។",
        "purchase_complete": (
            "✅ <b>ការទូទាត់បានជោគជ័យ!</b>\n"
            "ការបញ្ជាទិញ #{order_id} — {name} × {quantity}\n\n"
            "🔐 <b>ផលិតផលរបស់អ្នក៖</b>\n<code>{delivery}</code>\n\n"
            "សូមរក្សាសារនេះជាឯកជន។"
        ),
        "payment_caption": (
            "🧾 <b>ការបញ្ជាទិញ #{order_id}</b>\n"
            "📦 {name} × {quantity}\n"
            "💵 បង់ឱ្យបានត្រឹមត្រូវ៖ <b>{total}</b>\n"
            "🏦 គណនី៖ <b>{account_name}</b> ({account_number})\n\n"
            "ស្កេន BLINK KHQR ក្នុងផ្ទាំងនេះ ហើយបង់ប្រាក់ឱ្យបានត្រឹមត្រូវ។ "
            "បន្ទាប់មក ផ្ញើរូបថតបង្កាន់ដៃមកទីនេះ។ "
            "ការបញ្ជាទិញនេះផុតកំណត់ក្នុង {minutes} នាទី។"
        ),
        "payment_no_qr": (
            "🧾 <b>ការបញ្ជាទិញ #{order_id}</b>\n"
            "បង់ប្រាក់ <b>{total}</b> ទៅ {account_name} ({account_number})។\n\n"
            "⚠️ មិនទាន់បានកំណត់ QR ទេ។ សូមទាក់ទងអ្នកគ្រប់គ្រង។"
        ),
        "send_proof": "📸 ឥឡូវនេះ សូមផ្ញើរូបថតបង្កាន់ដៃទូទាត់។",
        "proof_received": "✅ បានទទួលបង្កាន់ដៃសម្រាប់ការបញ្ជាទិញ #{order_id}។ អ្នកគ្រប់គ្រងនឹងពិនិត្យឆាប់ៗ។",
        "proof_photo_only": "សូមផ្ញើបង្កាន់ដៃទូទាត់ជារូបថត។",
        "order_expired": "⌛ ការបញ្ជាទិញនេះបានផុតកំណត់។ សូមត្រឡប់ទៅហាង ហើយព្យាយាមម្ដងទៀត។",
        "orders_empty": "🧾 អ្នកមិនទាន់បានទិញអ្វីទេ — ចូលទៅហាង 🛍",
        "orders": "🧾 <b>ការបញ្ជាទិញរបស់ខ្ញុំ</b>\n\n{orders}",
        "order_line": "#{id} · {name} × {quantity}\n{total} · {status} · {date}",
        "deposit_prompt": "➕ ផ្ញើចំនួនទឹកប្រាក់ USD ដែលអ្នកចង់បញ្ចូល (ឧទាហរណ៍៖ <code>10</code>)។",
        "deposit_amount_invalid": "សូមផ្ញើចំនួនត្រឹមត្រូវ ឧទាហរណ៍៖ <code>10</code> ឬ <code>12.50</code>។",
        "deposit_caption": (
            "➕ <b>បញ្ចូលប្រាក់ #{deposit_id}</b>\n"
            "បង់ឱ្យបានត្រឹមត្រូវ៖ <b>{amount}</b>\n"
            "🏦 គណនី៖ <b>{account_name}</b> ({account_number})\n\n"
            "ស្កេន BLINK KHQR ក្នុងផ្ទាំងនេះ។ បន្ទាប់ពីបង់ សូមផ្ញើរូបថតបង្កាន់ដៃមកទីនេះ។"
        ),
        "deposit_auto_caption": (
            "➕ <b>ជួរបញ្ចូលប្រាក់ #{deposit_id}</b>\n"
            "ចំនួនដែលបានស្នើ៖ {requested_amount}\n"
            "💵 បង់ឱ្យបានត្រឹមត្រូវ៖ <b>{amount}</b>\n"
            "🔖 លេខសម្គាល់ជួរ៖ <code>TOPUP-{deposit_id}</code>\n"
            "🏦 គណនី៖ <b>{account_name}</b> ({account_number})\n\n"
            "ស្កេន BLINK KHQR ហើយបញ្ចូលចំនួនទឹកប្រាក់ឱ្យត្រឹមត្រូវ រួមទាំងខ្ទង់សេន។ "
            "ខ្ទង់សេនបន្ថែមគឺជាលេខកូដផ្ទៀងផ្ទាត់បណ្ដោះអាសន្ន មិនមែនថ្លៃសេវាទេ។ "
            "ទឹកប្រាក់ទាំងមូលដែលបានបង់នឹងត្រូវបញ្ចូលទៅសមតុល្យ ហើយសារ ABA នឹងផ្ទៀងផ្ទាត់ដោយស្វ័យប្រវត្តិ។ "
            "ជួរនេះផុតកំណត់ក្នុង {minutes} នាទី។ គណនីនីមួយៗអាចមានជួរសកម្មតែមួយប៉ុណ្ណោះ។\n\n"
            "បើមិនទាន់បានបញ្ជាក់ក្រោយពីប៉ុន្មាននាទី សូមប្រើប៊ូតុងខាងក្រោមដើម្បីផ្ញើបង្កាន់ដៃ។ "
            "សូមបោះបង់តែក្នុងករណីដែលអ្នកមិនទាន់បានបង់ប្រាក់ប៉ុណ្ណោះ។"
        ),
        "deposit_received": "✅ បានទទួលបង្កាន់ដៃបញ្ចូលប្រាក់។ អ្នកគ្រប់គ្រងនឹងពិនិត្យឆាប់ៗ។",
        "deposit_approved": (
            "✅ <b>ការទូទាត់បានជោគជ័យ!</b> ការបញ្ចូលប្រាក់ #{deposit_id} ត្រូវបានបញ្ជាក់។\n"
            "សមតុល្យថ្មី៖ {balance}។"
        ),
        "deposit_rejected": "❌ ការបញ្ចូលប្រាក់ #{deposit_id} ត្រូវបានបដិសេធ។ សូមទាក់ទងអ្នកគ្រប់គ្រង។",
        "submit_payment_proof": "📸 ផ្ញើបង្កាន់ដៃទូទាត់",
        "cancel_topup": "❌ បោះបង់ការបញ្ចូលប្រាក់",
        "topup_cancelled": "❌ បានបោះបង់ការបញ្ចូលប្រាក់ #{deposit_id}។ ឥឡូវអ្នកអាចបង្កើតថ្មីបាន។",
        "topup_already_cancelled": "ការបញ្ចូលប្រាក់នេះត្រូវបានបោះបង់រួចហើយ។",
        "topup_expired": "⌛ ការបញ្ចូលប្រាក់នេះបានផុតកំណត់។ សូមបង្កើតថ្មី។",
        "topup_failed": (
            "❌ <b>ការទូទាត់បរាជ័យ ឬផុតកំណត់</b>\n"
            "មិនបានទទួលការទូទាត់ ABA ដែលត្រូវគ្នាសម្រាប់ការបញ្ចូលប្រាក់ #{deposit_id} "
            "ក្នុងរយៈពេល {minutes} នាទី។ សមតុល្យរបស់អ្នកមិនត្រូវបានផ្លាស់ប្តូរទេ។ "
            "បើអ្នកបានបង់រួច សូមទាក់ទងអ្នកគ្រប់គ្រង។"
        ),
        "topup_not_pending": "ការបញ្ចូលប្រាក់នេះលែងរង់ចាំទៀតហើយ។",
        "topup_already_approved": "✅ ការបញ្ចូលប្រាក់នេះត្រូវបានបញ្ជាក់រួចហើយ។",
        "order_rejected": "❌ ការទូទាត់សម្រាប់ការបញ្ជាទិញ #{order_id} ត្រូវបានបដិសេធ។",
        "contact": "📞 ត្រូវការជំនួយ? ទាក់ទងអ្នកគ្រប់គ្រង៖ {support}",
        "language": "🌐 សូមជ្រើសរើសភាសា៖",
        "language_changed": "✅ បានប្ដូរភាសាទៅជាភាសាខ្មែរ។",
        "cancelled": "បានបោះបង់។",
        "already_processed": "សំណើនេះត្រូវបានដំណើរការរួចហើយ។",
        "menu_shop": "🛍 ហាង",
        "menu_balance": "💰 សមតុល្យ",
        "menu_deposit": "➕ បញ្ចូលប្រាក់",
        "menu_contact": "📞 ទាក់ទង",
        "menu_language": "🌐 ភាសា",
        "menu_orders": "🧾 ការបញ្ជាទិញ",
        "buy_one": "🛒 ទិញ 1 — {price}",
        "choose_qty": "🔢 ជ្រើសរើសចំនួន (1–{stock})",
        "back": "⬅️ ត្រឡប់ក្រោយ",
        "pay_now": "💳 បង់ឥឡូវ — {total}",
        "pay_balance": "👛 ទិញដោយសមតុល្យ — {total}",
        "status_awaiting_payment": "រង់ចាំការទូទាត់",
        "status_awaiting_review": "កំពុងពិនិត្យការទូទាត់",
        "status_completed": "បានបញ្ចប់",
        "status_rejected": "បានបដិសេធ",
        "status_cancelled": "បានបោះបង់",
    },
}


def tr(language: str | Language, key: str, **values: object) -> str:
    lang = language.value if isinstance(language, Language) else language
    template = TEXTS.get(lang, TEXTS["en"]).get(key, TEXTS["en"].get(key, key))
    return template.format(**values)
