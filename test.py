from pathlib import Path
from dotenv import load_dotenv
import os
load_dotenv(Path(r"C:\Users\User\Desktop\crypto_hub_nautilus\.env"))
print(repr(os.environ.get("TELEGRAM_BOT_TOKEN")))
print(repr(os.environ.get("TELEGRAM_CHAT_ID")))