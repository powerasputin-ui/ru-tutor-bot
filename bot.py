import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

# Храним историю диалога по chat_id в памяти (просто, но не переживает рестарт бота)
chat_histories = {}

SYSTEM_PROMPT = """Ты — дружелюбный репетитор русского языка для американцев, изучающих русский с нуля или на базовом уровне.

Правила:
1. Определи, на каком языке написал пользователь. Если он написал по-английски (или на смеси английского с русскими словами) — отвечай ПОЛНОСТЬЮ на английском.
2. Если пользователь написал фразу или попытку по-русски (даже с ошибками) — сначала кратко похвали попытку, потом на английском объясни:
   - что было написано правильно
   - какие есть ошибки (грамматика, падежи, ударения, порядок слов), укажи КОНКРЕТНО какое слово/место неверно и как правильно
   - дай правильный вариант фразы по-русски отдельной строкой
3. Если пользователь просто задаёт вопрос на английском (например "how do I say hello") — отвечай на английском, но давай русские примеры с транслитерацией и переводом.
4. Никогда не отвечай длинным текстом — максимум 4-6 предложений или короткий список. Это диалог в мессенджере, а не лекция.
5. Не используй markdown-заголовки, не пиши "Ответ:" — пиши как живой человек в чате.
6. Если сообщение — это попытка русского текста, обязательно в конце добавь строку вида:
   "✅ Правильно: <исправленный вариант>"
   Если ошибок нет — напиши "✅ Всё верно!"
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text(
        "Hi! I'm your Russian tutor bot 🇷🇺\n\n"
        "Write me anything — in English or try in Russian — and I'll help you learn. "
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
    history.append({"role": "user", "parts": [user_text]})

    # Собираем контекст: системный промпт + последние 10 сообщений истории
    convo = model.start_chat(history=[
        {"role": "user", "parts": [SYSTEM_PROMPT]},
        {"role": "model", "parts": ["Понял, буду следовать этим правилам."]},
    ] + history[-10:])

    try:
        response = convo.send_message(user_text)
        reply_text = response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        reply_text = "Sorry, something went wrong on my end. Try again in a moment."

    history.append({"role": "model", "parts": [reply_text]})
    chat_histories[chat_id] = history[-20:]  # ограничиваем историю, чтобы не раздувать контекст

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
