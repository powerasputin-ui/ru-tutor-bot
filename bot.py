import os
import logging
import sqlite3
from datetime import datetime
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

SYSTEM_PROMPT = """You are a friendly Russian language tutor for foreigners learning Russian from scratch or at a basic level.

Rules:
1. Detect what language the user wrote in (could be English, Spanish, German, or any other language). If they did NOT write in Russian, reply ENTIRELY in that same language (the language they used).
2. If the user wrote a phrase or attempt in Russian (even with mistakes) - keep it SHORT. Maximum 2-3 sentences total, not a lecture. Reply in English by default for the explanation (since Russian learners often understand English), unless the earlier conversation shows the user writes in another language, in which case use that language instead. Briefly confirm if it's correct, and only mention errors if there actually are any - do not explain things that are already correct.
3. If the user just asks a question in their language (e.g. "how do I say hello") - answer in that same language, and give the Russian example using the FORMAT rule below.
4. HARD LIMIT: never exceed 3 sentences or 40 words in a single reply. This is a messenger chat, not a lecture. If you have more to say, stop anyway.
5. Do not use markdown headers, do not write "Answer:" - write like a real person texting.
6. FORMAT for any Russian phrase you give the user (whether as an example, a correction, or confirming their own attempt): always write it as Latin transliteration FIRST, followed by the real Cyrillic spelling in square brackets right after. Example format: "Mne ty nravishsya [мне ты нравишься]". Never write a Russian phrase in only one of the two forms - always both, transliteration then brackets.
7. If the message is an attempt at Russian text, end your reply with a line in this exact format:
   "Правильно: " is not needed - instead just write "Correct: <transliteration> [<Cyrillic>]" if there was a mistake, using the format from rule 6.
   If there are no mistakes, just write "All correct!" and nothing more.
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
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.7,
            max_tokens=500,
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

    log_message(user.id, user.username, user.first_name, len(user_text))

    reply_text = await generate_reply(chat_id, user_text)
    await update.message.reply_text(reply_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    file_bytes = await voice_file.download_as_bytearray()

    try:
        transcription = client.audio.transcriptions.create(
            file=("voice.ogg", bytes(file_bytes)),
            model="whisper-large-v3",
            language="ru",
        )
        user_text = transcription.text
    except Exception as e:
        logger.error(f"Whisper error: {e}")
        await update.message.reply_text("Sorry, I couldn't understand the voice message. Try again or type it instead.")
        return

    if not user_text or not user_text.strip():
        await update.message.reply_text("I couldn't hear anything in that voice message. Try again?")
        return

    log_message(user.id, user.username, user.first_name, len(user_text))

    reply_text = await generate_reply(chat_id, user_text)
    await update.message.reply_text(f"I heard: \"{user_text}\"\n\n{reply_text}")


def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
