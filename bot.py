import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from groq import Groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
ADMIN_USER_ID = 35049

client = Groq(api_key=GROQ_API_KEY)
MODEL_NAME = "llama-3.3-70b-versatile"

chat_histories = {}
user_message_times = {}
chat_last_active = {}
RATE_LIMIT_MESSAGES = 10
RATE_LIMIT_WINDOW = timedelta(minutes=1)
INACTIVITY_TTL = timedelta(hours=6)
MAX_MESSAGE_LENGTH = 2000
MAX_VOICE_SECONDS = 120


def is_rate_limited(user_id):
    now = datetime.utcnow()
    times = user_message_times.get(user_id, [])
    times = [t for t in times if now - t < RATE_LIMIT_WINDOW]
    if len(times) >= RATE_LIMIT_MESSAGES:
        user_message_times[user_id] = times
        return True
    times.append(now)
    user_message_times[user_id] = times
    return False


async def cleanup_stale_data():
    while True:
        await asyncio.sleep(3600)
        now = datetime.utcnow()
        stale_chats = [cid for cid, t in chat_last_active.items() if now - t > INACTIVITY_TTL]
        for cid in stale_chats:
            chat_histories.pop(cid, None)
            chat_last_active.pop(cid, None)
        stale_users = [uid for uid, times in user_message_times.items() if not times or now - max(times) > INACTIVITY_TTL]
        for uid in stale_users:
            user_message_times.pop(uid, None)
        if stale_chats or stale_users:
            logger.info(f"Cleanup: removed {len(stale_chats)} stale chats, {len(stale_users)} stale rate-limit entries")

DB_PATH = os.environ.get("DB_PATH", "stats.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            message_length INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def log_message(user_id, username, first_name, message_length):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (user_id, username, first_name, message_length, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, first_name, message_length, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_stats():
    conn = sqlite3.connect(DB_PATH)
    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM messages").fetchone()[0]
    top_users = conn.execute(
        "SELECT username, first_name, COUNT(*) as cnt FROM messages GROUP BY user_id ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    conn.close()
    return total_messages, total_users, top_users

SYSTEM_PROMPT = """You are a friendly Russian language tutor for foreigners learning Russian from scratch or at a basic level. You talk like a warm, encouraging private tutor chatting on Telegram, not a textbook.

Rules:
1. Detect what language the user wrote in. If they did NOT write in Russian, reply ENTIRELY in that same language (default to English if unsure).
2. If the user asks how to say/translate something, teach it properly rather than just giving a bare translation: give the main phrase, briefly explain when/how it's used, and if there's a useful related variant (e.g. formal vs informal), mention it too. End by inviting them to try using it.
3. If the user wrote a phrase or attempt in Russian (even with mistakes, even transliterated) - confirm if it's correct, explain briefly if there's a mistake, and keep the conversation going naturally.
4. Target length: roughly 60-120 words. Multiple short paragraphs are fine and encouraged for readability. Don't pad for the sake of length, but don't be a bare one-liner either - always give enough context to actually be useful.
5. Do not use markdown headers, do not write "Answer:", no emojis.
6. FORMAT: every Russian phrase you write, anywhere in your reply, must be written as Latin transliteration first, then the real Cyrillic spelling in square brackets right after, as one unit - e.g. "kak dela [как дела]". Never write Russian in only Cyrillic or only Latin alone. Never use round parentheses for this - always square brackets. Use this EXACT transliteration for these letters every single time, with zero variation: ы=y (so "ты" is always "ty", never "ti"), и=i, й=y, ю=yu, я=ya, х=kh, ц=ts, ч=ch, ш=sh, щ=shch, ж=zh, е=ye/e, э=e. Be fully consistent within and across replies - the same Russian word must always get the same transliteration spelling, never mix variants like "ti" and "ty" for the same word.
7. If the message was the user's own attempt at Russian, end your reply with a line: "Correct: <phrase in FORMAT>" only if there was a mistake. If there were no mistakes, just write "All correct!" and continue naturally. Never add this line when you were just answering a translation question (rule 2) instead of correcting an attempt.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text(
        "Hi! I'm your Russian tutor bot\n\n"
        "Write me anything - in English or try in Russian - and I'll help you learn. "
        "You can also send voice messages! Try typing or saying something in Russian, even with mistakes.\n\n"
        "Send /reset to clear our conversation history."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text("History cleared. Let's start fresh!")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    total_messages, total_users, top_users = get_stats()
    lines = [f"Total messages: {total_messages}", f"Total users: {total_users}", "", "Top users:"]
    for username, first_name, cnt in top_users:
        name = f"@{username}" if username else (first_name or "unknown")
        lines.append(f"{name}: {cnt} messages")
    await update.message.reply_text("\n".join(lines))


async def generate_reply(chat_id, user_text):
    history = chat_histories.get(chat_id, [])
    history.append({"role": "user", "content": user_text})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-10:]

    try:
        loop = asyncio.get_running_loop()
        completion = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.3,
                max_tokens=500,
            ),
        )
        reply_text = completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq error: {e}")
        reply_text = "Sorry, something went wrong on my end. Try again in a moment."

    history.append({"role": "assistant", "content": reply_text})
    chat_histories[chat_id] = history[-20:]
    return reply_text


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    user = update.effective_user

    if is_rate_limited(user.id):
        await update.message.reply_text("You're sending messages too fast! Please wait a moment.")
        return

    if len(user_text) > MAX_MESSAGE_LENGTH:
        await update.message.reply_text(f"That message is too long ({len(user_text)} chars). Please keep it under {MAX_MESSAGE_LENGTH} characters.")
        return

    chat_last_active[chat_id] = datetime.utcnow()
    log_message(user.id, user.username, user.first_name, len(user_text))

    reply_text = await generate_reply(chat_id, user_text)
    await update.message.reply_text(reply_text, reply_to_message_id=update.message.message_id)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    if is_rate_limited(user.id):
        await update.message.reply_text("You're sending messages too fast! Please wait a moment.")
        return

    if update.message.voice.duration > MAX_VOICE_SECONDS:
        await update.message.reply_text(f"That voice message is too long. Please keep it under {MAX_VOICE_SECONDS} seconds.")
        return

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    file_bytes = await voice_file.download_as_bytearray()

    try:
        loop = asyncio.get_running_loop()
        transcription = await loop.run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(
                file=("voice.ogg", bytes(file_bytes)),
                model="whisper-large-v3",
            ),
        )
        user_text = transcription.text
    except Exception as e:
        logger.error(f"Whisper error: {e}")
        await update.message.reply_text("Sorry, I couldn't understand the voice message. Try again or type it instead.")
        return

    if not user_text or not user_text.strip():
        await update.message.reply_text("I couldn't hear anything in that voice message. Try again?")
        return

    chat_last_active[chat_id] = datetime.utcnow()
    log_message(user.id, user.username, user.first_name, len(user_text))

    reply_text = await generate_reply(chat_id, user_text)
    await update.message.reply_text(f"I heard: \"{user_text}\"\n\n{reply_text}", reply_to_message_id=update.message.message_id)


async def start_background_tasks(application):
    application.create_task(cleanup_stale_data())


def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(start_background_tasks).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
