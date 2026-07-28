import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from groq import Groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

client = Groq(api_key=GROQ_API_KEY)
MODEL_NAME = "llama-3.3-70b-versatile"

chat_histories = {}

SYSTEM_PROMPT = """You are a friendly Russian language tutor for foreigners learning Russian from scratch or at a basic level.

Rules:
1. Detect what language the user wrote in (could be English, Spanish, German, or any other language). If they did NOT write in Russian, reply ENTIRELY in that same language (the language they used).
2. If the user wrote a phrase or attempt in Russian (even with mistakes) - keep it SHORT. Maximum 2-3 sentences total, not a lecture. Reply in English by default for the explanation (since Russian learners often understand English), unless the earlier conversation shows the user writes in another language, in which case use that language instead. Briefly confirm if it's correct, and only mention errors if there actually are any - do not explain things that are already correct.
3. If the user just asks a question in their language (e.g. "how do I say hello" or its equivalent in another language) - answer in that same language, but give Russian examples with transliteration and translation.
4. HARD LIMIT: never exceed 3 sentences or 40 words in a single reply. This is a messenger chat, not a lecture. If you have more to say, stop anyway.
5. Do not use markdown headers, do not write "Answer:" - write like a real person texting.
6. If the message is an attempt at Russian text, end your reply with a line in Cyrillic (not transliterated):
   "Правильно: <corrected variant>" - only include this line if there was an actual mistake.
   If there are no mistakes, just write "Всё верно!" in Cyrillic and nothing more.
7. Never use Latin transliteration for Russian words in your output - always write actual Russian words in Cyrillic script.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text(
        "Hi! I'm your Russian tutor bot\n\n"
        "Write me anything - in English or try in Russian - and I'll help you learn. "
        "Try typing something in Russian, even with mistakes!\n\n"
        "Send /reset to clear our conversation history."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text("History cleared. Let's start fresh!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text

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

    await update.message.reply_text(reply_text)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
