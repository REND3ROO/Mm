import logging
import httpx
import re
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, JobQueue
)
import asyncio
import nest_asyncio

# ✅ Apply nest_asyncio patch for Termux or PythonAnywhere
nest_asyncio.apply()

# ✅ Bot Token
BOT_TOKEN = '8407972432:AAG_soDI2Ypi0Q4KigOqBRaLfCN06LdgEPA'

# ✅ Admins
ADMINS = {1583156507, 1963166427}

# ✅ DB Setup
conn = sqlite3.connect("subscriptions.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    expiry_date TEXT
)""")
conn.commit()

# ✅ Logger
logging.basicConfig(level=logging.INFO)

# ✅ Global State
user_state = {}

# ✅ API Endpoints
API_BASES = {
    "deep_scan": "https://api.elitepredator.app/deep_search?mobile=",
    "aadhaar_search": "https://api.elitepredator.app/search_id?aadhaar=",
    "mobile_search": "https://api.elitepredator.app/search_mobile?mobile="
}

# ✅ Address Cleaner
def clean_address(raw_address: str) -> str:
    if not raw_address:
        return "Not Available"
    cleaned = re.sub(r"[!]+", ", ", raw_address)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    return cleaned.strip(" ,")

# ✅ Result Formatter
def format_multi_results(data: dict) -> str:
    if not data or "results" not in data:
        return "No data found or response invalid."
    results = data.get("results", [])
    aadhaar = data.get("aadhaar", "N/A")
    seen = {
        "Mobile Number": set(),
        "Address": set(),
        "Sim/State": set()
    }
    output = []
    valid_count = 0
    for result in results:
        if not isinstance(result, dict):
            continue
        valid_count += 1
        output.append(f"========[ DOXXED RECORD #{valid_count} ]========")
        fields = {
            "Mobile Number": result.get("Mobile Number", "Not Available"),
            "Name": result.get("Name", "Not Available"),
            "Father/Husband": result.get("Father/Husband", "Not Available"),
            "Address": clean_address(result.get("Address")),
            "Alt Number": result.get("Alt Number", "Not Available"),
            "Sim/State": result.get("Sim/State", "Not Available"),
            "Aadhaar Card": result.get("Aadhaar Card", "Not Available"),
            "Email Address": result.get("Email Address", "Not Available") or "Not Available"
        }
        for key, val in fields.items():
            output.append(f"{key:<20}: {val}")
            if key in seen and val != "Not Available":
                seen[key].add(val)
        output.append("")
    summary = [
        "---------- SUMMARY ----------",
        f"Total Records         : {valid_count}",
        f"Unique Mobiles        : {len(seen['Mobile Number'])}",
        f"Unique Addresses      : {len(seen['Address'])}",
        f"Unique SIM/States     : {len(seen['Sim/State'])}",
        f"Aadhaar Linked        : {aadhaar}",
        "-----------------------------"
    ]
    return "\n".join(output + summary)

# ✅ Check Access
def is_authorized(user_id):
    cursor.execute("SELECT expiry_date FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        expiry = datetime.strptime(row[0], "%Y-%m-%d")
        return expiry >= datetime.now()
    return False

# ✅ /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_authorized(user_id) and user_id not in ADMINS:
        await update.message.reply_text("Access denied. Subscription required.")
        return
    keyboard = [
        [InlineKeyboardButton("Deep Scan (Mobile)", callback_data='deep_scan'),
         InlineKeyboardButton("Aadhaar Search", callback_data='aadhaar_search')],
        [InlineKeyboardButton("Mobile Number Search", callback_data='mobile_search')]
    ]
    await update.message.reply_text("Select search type:", reply_markup=InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("OSINT Bot by @ransomxrend3ro")

# ✅ Callback handler
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    if not is_authorized(user_id) and user_id not in ADMINS:
        await update.callback_query.message.reply_text("Access denied.")
        return
    query = update.callback_query
    await query.answer()
    user_state[user_id] = query.data
    prompts = {
        "deep_scan": "Enter mobile number for deep scan:",
        "aadhaar_search": "Enter Aadhaar number:",
        "mobile_search": "Enter mobile number to search:"
    }
    await query.message.reply_text(prompts.get(query.data, "Enter input:"))

# ✅ Handle input
async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_authorized(user_id) and user_id not in ADMINS:
        await update.message.reply_text("Access denied.")
        return

    user_input = update.message.text.strip()
    search_type = user_state.get(user_id)
    if not search_type:
        await update.message.reply_text("Please use /start to begin.")
        return

    url = API_BASES[search_type] + user_input
    await update.message.reply_text("Loading Info...")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url)

        status = response.status_code

        if status == 400:
            await update.message.reply_text("❌ Invalid input. Please check and try again.")
            return
        elif status in (401, 403):
            await update.message.reply_text("❌ You are not authorized to access this resource.")
            return
        elif status == 404:
            await update.message.reply_text("❌ No records found.")
            return
        elif status == 429:
            await update.message.reply_text("⚠️ Too many requests. Please wait and try again.")
            return
        elif status >= 500:
            await update.message.reply_text("❌ Server error. Please try again later.")
            return

        try:
            data = response.json()
        except Exception:
            await update.message.reply_text("❌ Failed to decode response. Server returned invalid data.")
            return

    except httpx.RequestError:
        await update.message.reply_text("❌ Network error. Check your connection or try again later.")
        return
    except Exception:
        await update.message.reply_text("❌ Unexpected error. Please try again.")
        return

    result_text = format_multi_results(data)
    if len(result_text) <= 4000:
        await update.message.reply_text(f"```\n{result_text}\n```", parse_mode="Markdown")
    else:
        for i in range(0, len(result_text), 4000):
            await update.message.reply_text(f"```\n{result_text[i:i+4000]}\n```", parse_mode="Markdown")

# ✅ /add
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in ADMINS:
        return
    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        expiry = datetime.now() + timedelta(days=days)
        cursor.execute("INSERT OR REPLACE INTO users (user_id, expiry_date) VALUES (?, ?)",
                       (target_id, expiry.strftime("%Y-%m-%d")))
        conn.commit()
        await update.message.reply_text(f"✅ Subscription given to {target_id} for {days} days.")
    except:
        await update.message.reply_text("Usage: /add <userid> <days>")

# ✅ /listuser
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in ADMINS:
        return
    cursor.execute("SELECT * FROM users")
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("No users found.")
        return
    message = "🧾 Subscribed Users:\n\n"
    for uid, exp in rows:
        remaining = (datetime.strptime(exp, "%Y-%m-%d") - datetime.now()).days
        message += f"👤 UserID: {uid} | Days Left: {remaining}\n"
    await update.message.reply_text(message)

# ✅ /remove
async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in ADMINS:
        return
    try:
        target_id = int(context.args[0])
        cursor.execute("DELETE FROM users WHERE user_id = ?", (target_id,))
        conn.commit()
        await update.message.reply_text(f"❌ Subscription removed for {target_id}.")
    except:
        await update.message.reply_text("Usage: /remove <userid>")

# ✅ Daily subscription expiry check
async def check_expired(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    cursor.execute("SELECT user_id, expiry_date FROM users")
    rows = cursor.fetchall()
    for user_id, exp in rows:
        expiry = datetime.strptime(exp, "%Y-%m-%d")
        if expiry < now:
            try:
                await context.bot.send_message(chat_id=user_id, text="⏳ Your subscription has ended.")
            except:
                pass
            cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            conn.commit()

# ✅ Main runner
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_user))
    app.add_handler(CommandHandler("listuser", list_users))
    app.add_handler(CommandHandler("remove", remove_user))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))

    # ✅ Add the job queue AFTER app is initialized
    async def post_init(application):
        application.job_queue.run_repeating(check_expired, interval=86400, first=10)

    app.post_init = post_init

    print("✅ Bot is live and running.")
    await app.run_polling()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
