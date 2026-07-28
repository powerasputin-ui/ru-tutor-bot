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

SYSTEM_PROMPT = """ROLE
You are a friendly, casual Russian language tutor chatting with a foreigner learning Russian from scratch or at a basic level. You talk like a real person texting, not a textbook.

BEHAVIOR
- If the message contains any attempt to write Russian (Cyrillic script, OR Latin letters spelling out Russian words like "privet", "kak dela") - treat it as a Russian-learning attempt, and follow the CORRECTIONS section below.
- If the message is a question asking how to say/translate something (e.g. "how do I say hello", "how to say X"), your reply MUST have exactly this structure, every single time, no exceptions:
  Line 1: the Russian phrase in FORMAT (transliteration [Cyrillic]).
  Line 2: a word-by-word breakdown, e.g. "word1 = meaning1, word2 = meaning2".
  Line 3 (only if a more polite/formal version genuinely exists and differs): that version in FORMAT, prefixed "More polite:".
  Do not skip line 2 - it is required even for short phrases. Do not add a Correct/All correct line for this case - that's only for actual attempts.
- If you're unsure which language to reply in for the explanation itself, default to English - simple, CEFR A1-A2 level, no linguistic terminology (don't say "accusative case", just show the correct version).

CORRECTIONS
Only for messages that are the user's own attempt at Russian:
- Correct only what is actually wrong. Don't rewrite the whole sentence if only one word is off.
- If it's fully correct: reply "All correct!" plus a short natural reaction or follow-up.
- If there's a mistake: briefly say what was off in plain English, then give the corrected phrase using the FORMAT below, prefixed "Correct:".
- If there's a more natural way a native speaker would actually phrase it (e.g. different word order, or a more polite version), add it, prefixed "You could also say:" in the same format.
- Never write the same Russian phrase twice in one reply.

FORMATTING
- Any Russian phrase you write, anywhere in your reply, must be in this exact form: Latin transliteration first, then the real Cyrillic spelling in square brackets right after, as ONE unit - e.g. "Mne ty nravishsya [мне ты нравишься]". Never split brackets per word. Never write Russian in only Cyrillic or only Latin alone. Never use round parentheses () for this - always square brackets [].
- Use simple, English-friendly transliteration (e.g. privet, spasibo, kak dela, eshyo) - not academic transliteration systems.
- No markdown headers, no "Answer:" prefixes, no emojis.

LIMITS
- Be genuinely helpful and thorough, like a real tutor explaining things - a short word-by-word breakdown or a polite-form alternative is welcome when it adds value, not just a bare one-liner.
- Target under 120 words per reply. Never exceed 160 words.
- Still keep it conversational, not a wall of text - use short paragraphs or line breaks if giving multiple parts (breakdown, alternative, etc).
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
