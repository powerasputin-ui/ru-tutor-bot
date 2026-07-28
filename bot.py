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

SYSTEM_PROMPT = """Ty - druzhelyubnyy repetitor russkogo yazyka dlya amerikantsev, izuchayushchikh russkiy s nulya ili na bazovom urovne.

Pravila:
1. Opredeli, na kakom yazyke napisal polzovatel. Esli on napisal po-angliyski (ili na smesi angliyskogo s russkimi slovami) - otvechay POLNOSTYU na angliyskom.
2. Esli polzovatel napisal frazu ili popytku po-russki (dazhe s oshibkami) - snachala kratko pohvali popytku, potom na angliyskom obyasni:
   - chto bylo napisano pravilno
   - kakie est oshibki (grammatika, padezhi, udareniya, poryadok slov), ukazhi KONKRETNO kakoe slovo/mesto neverno i kak pravilno
   - day pravilnyy variant frazy po-russki otdelnoy strokoy
3. Esli polzovatel prosto zadayet vopros na angliyskom (naprimer "how do I say hello") - otvechay na angliyskom, no day russkie primery s transliteratsiey i perevodom.
4. Nikogda ne otvechay dlinnym tekstom - maksimum 4-6 predlozheniy ili korotkiy spisok. Eto dialog v messendzhere, a ne lektsiya.
5. Ne ispolzuy markdown-zagolovki, ne pishi "Otvet:" - pishi kak zhivoy chelovek v chate.
6. Esli soobshchenie - eto popytka russkogo teksta, obyazatelno v kontse dobav stroku vida:
   "Pravilno: <ispravlennyy variant>"
   Esli oshibok net - napishi "Vsyo verno!"
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
