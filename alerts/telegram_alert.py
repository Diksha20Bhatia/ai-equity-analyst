"""
telegram_alert.py  —  "The Messenger"
=====================================
Sends the final shortlist to your Telegram. If no bot token / chat id is
configured, it just prints to the screen — the pipeline never breaks.
"""

from config import settings


def send(text: str):
    token = settings.telegram_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        print("\n[telegram] (not configured — showing here instead)\n")
        print(text)
        return

    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        if resp.status_code == 200:
            print("[telegram] alert sent ✅")
        else:
            print(f"[telegram] failed ({resp.status_code}): {resp.text}")
            print(text)
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] error: {e}")
        print(text)
