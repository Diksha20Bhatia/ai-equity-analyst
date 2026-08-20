"""
telegram_alert.py  —  "The Messenger"
=====================================
Sends the final shortlist to your Telegram. If no bot token / chat id is
configured, it just prints to the screen — the pipeline never breaks.

Telegram rejects any single message over 4096 characters (400 Bad Request:
"message is too long"). A shortlist with several stocks easily exceeds that,
so long alerts are split into multiple messages at paragraph boundaries
(never mid-stock, so Markdown bold/italic markers always stay matched).
"""

from config import settings

MAX_LEN = 4000  # stay safely under Telegram's 4096 hard limit


def _chunk(text: str, max_len: int = MAX_LEN) -> list:
    paragraphs = text.split("\n\n")
    chunks, current = [], ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(para) <= max_len:
            current = para
        else:
            # a single paragraph is itself too long — hard-split it
            for i in range(0, len(para), max_len):
                chunks.append(para[i:i + max_len])
            current = ""
    if current:
        chunks.append(current)
    return chunks


def send(text: str):
    token = settings.telegram_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        print("\n[telegram] (not configured — showing here instead)\n")
        print(text)
        return

    import requests
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks, 1):
        part = chunk if len(chunks) == 1 else f"({i}/{len(chunks)})\n\n{chunk}"
        try:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "text": part, "parse_mode": "Markdown"},
                timeout=15,
            )
            if resp.status_code == 200:
                print(f"[telegram] alert sent ✅ ({i}/{len(chunks)})")
            else:
                print(f"[telegram] failed ({resp.status_code}): {resp.text}")
                print(part)
        except Exception as e:  # noqa: BLE001
            print(f"[telegram] error: {e}")
            print(part)
