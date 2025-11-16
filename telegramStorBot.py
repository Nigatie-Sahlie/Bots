
import os
import sys
from dotenv import load_dotenv
import telebot
import requests
import csv
import time
import logging
import argparse

# Load environment variables from a .env file
load_dotenv()

# Read the Telegram bot token from env var instead of hardcoding it
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    print("Missing environment variable: TELEGRAM_BOT_TOKEN.\nCreate a .env file with TELEGRAM_BOT_TOKEN=your_token or set the variable in your environment.")
    sys.exit(1)

# Parse CLI args for webhook helpers (show/clear)
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--show-webhook", action="store_true", help="Print getWebhookInfo and exit")
parser.add_argument("--clear-webhook", action="store_true", help="Call deleteWebhook and exit (prompts for confirmation unless --force-clear is set)")
parser.add_argument("--show-and-clear", action="store_true", help="Show webhook info then clear it and exit (prompts for confirmation unless --force-clear is set)")
parser.add_argument("--force-clear", action="store_true", help="Skip confirmation when clearing webhook")
cli_args, _ = parser.parse_known_args()


def _get_webhook_info():
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")
        print("getWebhookInfo:", r.status_code)
        print(r.text)
    except Exception as e:
        print("Failed to call getWebhookInfo:", e)


def _clear_webhook():
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
        print("deleteWebhook:", r.status_code)
        print(r.text)
    except Exception as e:
        print("Failed to call deleteWebhook:", e)


if cli_args.show_webhook:
    _get_webhook_info()
    sys.exit(0)


if cli_args.clear_webhook:
    if not cli_args.force_clear:
        ans = input("Are you sure you want to delete the webhook? Type 'yes' to confirm: ")
        if ans.strip().lower() != "yes":
            print("Aborted deleteWebhook.")
            sys.exit(0)
    _clear_webhook()
    sys.exit(0)


if cli_args.show_and_clear:
    _get_webhook_info()
    if not cli_args.force_clear:
        ans = input("Proceed to delete the webhook? Type 'yes' to confirm: ")
        if ans.strip().lower() != "yes":
            print("Aborted deleteWebhook.")
            sys.exit(0)
    _clear_webhook()
    sys.exit(0)

bot = telebot.TeleBot(BOT_TOKEN)
# Optional owner chat id - if set, the bot will send a startup notification to this chat id
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")
# Per-user echo toggle (default: enabled)
echo_enabled = {}

# Auto-clear webhook on startup? Set AUTO_CLEAR_WEBHOOK=false to disable
AUTO_CLEAR_WEBHOOK = os.getenv("AUTO_CLEAR_WEBHOOK", "true").lower() in ("1", "true", "yes")

# Configure logging to file and console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Questions you want to ask the user
questions = [
    "what is your name?",
    "your Department?",
    "your phone number?",
    "about the bot?"
]

# Dictionary to store user responses temporarily
user_data = {}


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id

    user_data[user_id] = {
        "step": 0,
        "answers": []
    }

    bot.send_message(user_id, "👋 Welcome to *ScorePlus*!\nLet's collect your details.")
    bot.send_message(user_id, questions[0])


@bot.message_handler(commands=['status'])
def status(message):
    """Reply to user asking for bot status."""
    try:
        bot.send_message(message.chat.id, "✅ Bot is running and connected to Telegram.")
    except Exception as e:
        # If sending fails, print to console for debugging
        logging.exception("Failed to reply to /status request:")


@bot.message_handler(commands=['echooff'])
def echo_off(message):
    user_id = message.chat.id
    echo_enabled[user_id] = False
    bot.send_message(user_id, "Echo turned OFF for your session.")


@bot.message_handler(commands=['echoon'])
def echo_on(message):
    user_id = message.chat.id
    echo_enabled[user_id] = True
    bot.send_message(user_id, "Echo turned ON for your session.")


@bot.message_handler(func=lambda m: True)
def handle_response(message):
    user_id = message.chat.id

    # Log incoming message
    try:
        logging.info(f"Received message from {user_id}: {message.text}")
    except Exception:
        logging.info(f"Received message from {user_id}: <unprintable>")

    # Ignore messages from users who haven't started
    if user_id not in user_data:
        bot.send_message(user_id, "Please type /start to begin.")
        return

    # Echo the user's input back to them (limited length) if echo enabled
    try:
        if echo_enabled.get(user_id, True):
            echo_text = message.text or ""
            if len(echo_text) > 1000:
                echo_text = echo_text[:1000] + "..."
            bot.send_message(user_id, f"You said: {echo_text}")
    except Exception:
        logging.exception("Failed to echo message to user")

    step = user_data[user_id]["step"]
    user_data[user_id]["answers"].append(message.text)

    # Move to next question
    step += 1
    user_data[user_id]["step"] = step

    if step < len(questions):
        bot.send_message(user_id, questions[step])
    else:
        bot.send_message(user_id, "✅ Thank you! Your information has been saved.")
        save_to_csv(user_id)
        del user_data[user_id]


def save_to_csv(user_id):
    """Save user answers to a CSV file."""
    try:
        with open("scoreplus_users.csv", "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(user_data[user_id]["answers"])
        print(f"Saved answers for user {user_id} to scoreplus_users.csv")
    except Exception as e:
        print(f"Failed to save answers for user {user_id}:", e)


# --- Startup / polling (module-level) ---
try:
    # Clear any webhook (prevents "Conflict: terminated by other getUpdates request")
    try:
        bot.remove_webhook()
    except Exception:
        pass

    # Optionally show webhook info and explicitly call Telegram deleteWebhook endpoint as a fallback
    if AUTO_CLEAR_WEBHOOK:
        try:
            info = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")
            logging.info("getWebhookInfo response: %s %s", info.status_code, info.text)
        except Exception:
            logging.exception("Failed to call getWebhookInfo")

        # Attempt to delete webhook with retries
        for attempt in range(1, 4):
            try:
                resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
                logging.info("deleteWebhook attempt %d: %s %s", attempt, resp.status_code, resp.text)
                if resp.status_code == 200:
                    break
            except Exception:
                logging.exception("deleteWebhook attempt %d failed", attempt)
            time.sleep(1 * attempt)
    else:
        logging.info("AUTO_CLEAR_WEBHOOK is disabled; skipping webhook info and deleteWebhook steps.")

    print("Bot polling started...")

    # If OWNER_CHAT_ID is set, send a startup notification to that chat id
    if OWNER_CHAT_ID:
        try:
            owner_id = int(OWNER_CHAT_ID)
            bot.send_message(owner_id, "✅ Bot has started and is now polling.")
            print(f"Startup notification sent to OWNER_CHAT_ID={owner_id}")
        except Exception as e:
            print("Could not send startup notification to OWNER_CHAT_ID:", e)

    # Use infinity_polling to automatically restart on some errors
    try:
        bot.infinity_polling(timeout=20, long_polling_timeout=20)
    except Exception:
        logging.exception("infinity_polling terminated with exception")
except Exception as e:
    print("Bot polling failed:", e)
