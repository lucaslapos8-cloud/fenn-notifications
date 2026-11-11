"""Direct links to the chat a notification came from, for apps whose
notifications don't say which chat they belong to.

Most apps put everything needed into the notification itself (WhatsApp,
Telegram, Teams, ...), so clicking it again always lands in the right chat.
Apps built on Electron (Beeper, and many others) only remember a
notification until it is clicked once; after that a click can only bring
the app up. For those, a helper here can work out the chat from the app's
own data and return a link the app opens directly.

Each helper gets the notification (app id, title, text, arrival time) and
returns a link or None. Helpers only read, never write, and nothing leaves
the computer. To support another app, add a function and list it in
HELPERS with the app's AppUserModelID.
"""

import json
import os
import sqlite3
import urllib.parse

_cache = {}  # notification order -> link, worked out once


def chat_link(note):
    """A link that opens the notification's chat, or None."""
    key = note["order"]
    if key not in _cache:
        helper = HELPERS.get(note["aumid"].lower())
        if not helper:
            return None
        try:
            link = helper(note)
        except Exception:  # an app update changed its data: just no link
            link = None
        if not link:
            return None  # maybe not written yet: try again on the next click
        _cache[key] = link
    return _cache[key]


# ---------------------------------------------------------------------------
# Beeper: its chat list and messages in %APPDATA%\BeeperTexts\index.db.
# The notification's title is the sender (or the group), its text the
# message; the message that arrived at that time from that sender gives
# the chat, and beeper://select-thread/<network>/<chat>?accountID=... opens
# it (the link Beeper itself copies with "Copy > Chat Link").

BEEPER_DB = os.path.expandvars(r"%APPDATA%\BeeperTexts\index.db")


def beeper(note):
    if not os.path.isfile(BEEPER_DB):
        return None
    info = note["info"]
    title, body = (info["title"] or "").strip(), (info["body"] or "").strip()
    if not title:
        return None
    ms = int(note["at"] * 1000)
    db = sqlite3.connect(f"file:{BEEPER_DB}?mode=ro", uri=True, timeout=1.0)
    try:
        best = None  # (text matches, closeness, chat, account)
        for room, ts, msg in db.execute(
                "select roomID, timestamp, message from mx_room_messages "
                "where timestamp between ? and ? order by abs(timestamp - ?) limit 200",
                (ms - 120_000, ms + 30_000, ms)):
            try:
                m = json.loads(msg) if msg else {}
            except ValueError:
                continue
            if m.get("isSender"):
                continue  # your own message never makes a notification
            row = db.execute("select accountID, json_extract(thread, '$.title') from threads where threadID = ?",
                             (room,)).fetchone()
            if not row:
                continue
            account, chat_title = row[0], (row[1] or "").strip()
            names = {n.strip() for n in db.execute(
                "select full_name, nickname from participants where room_id = ? and id = ?",
                (room, m.get("senderID"))).fetchone() or () if n}
            if not (title in names or title == chat_title or any(n in title for n in names if len(n) > 2)
                    or (len(chat_title) > 2 and chat_title in title)):
                continue  # someone else's message
            text = (m.get("text") or "").strip()
            matches = bool(text) and bool(body) and (text == body or body in text or text in body)
            score = (matches, -abs(ts - ms))
            if best is None or score > best[0]:
                best = (score, room, account)
        if not best:
            return None
        _, room, account = best
        network = account.split(".", 1)[0]
        if network.startswith("$"):
            return None
        return (f"beeper://select-thread/{urllib.parse.quote(network, safe='')}/{urllib.parse.quote(room, safe='')}"
                f"?accountID={urllib.parse.quote(account, safe='')}")
    finally:
        db.close()


HELPERS = {
    "com.automattic.beeper.desktop": beeper,
}
