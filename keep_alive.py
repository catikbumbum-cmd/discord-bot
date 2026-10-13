
from flask import Flask
from threading import Thread

web_app = Flask('')

@web_app.route('/')
def home():
    return "Bot is alive! ✅"

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    Thread(target=lambda: web_app.run(host='0.0.0.0', port=port), daemon=True).start()

if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN missing — set it in Render → Environment")
    else:
        load_settings()
        keep_alive()   # ← MUST come BEFORE bot.run!
        bot.run(TOKEN)