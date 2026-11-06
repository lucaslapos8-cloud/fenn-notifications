"""Notifications: every Windows toast pops out of the island instead of the
corner of the screen. A small bubble grows out of the pill with the app,
sender and message, shrinks back into it after a few seconds, and a click
opens the exact chat or page the toast came from (same as clicking the
Windows toast).

The bubble is its own little window (bubble.ps1) drawn over Fenn's pill, so
Fenn itself never opens or pins. The plugin's page, opened from Fenn, lists
recent notifications and the settings."""

import base64
import ctypes
import os
import subprocess
import sys
import threading
import time

import fenn
import winnotif as wn

# Fenn's protocol is UTF-8 JSON lines. Fenn sets PYTHONIOENCODING for plugins,
# but don't rely on it: emoji in a message would otherwise break the output.
for _stream in (sys.stdout, sys.stdin):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

app = fenn.Plugin()
feed = wn.NotificationFeed()
cache = os.path.join(app.data_dir, "cache")
os.makedirs(cache, exist_ok=True)

DEFAULTS = {"popup": True, "seconds": 6, "hide_banners": False,
            "muted": [], "banner_changed": [], "fullscreen": False,
            "style": "tinted", "size": 2, "tint": 0,
            "photos": True, "pill_badge": 1, "marker": 1,
            "sound_on": False, "sound": "b:message", "volume": 70, "app_sounds": {},
            "custom_sounds": [], "mute_windows_sounds": False, "saved_sounds": {},
            "app_volumes": {}}
settings = {**DEFAULTS, **(app.load("settings", {}) or {})}
DURATIONS = [4, 6, 10, 20]
STYLES = ["solid", "glass", "tinted"]
STYLE_NAMES = ["Solid", "Glass", "Tinted"]
SIZE_NAMES = ["XS", "S", "M", "L", "XL"]  # XS is as small as Fenn's own pill
TINT_NAMES = ["Full", "High", "Medium", "Light"]
BADGE_NAMES = ["Off", "10 s", "Until read"]  # how long the unread marker stays
MARKERS = ["dot", "count", "fenn"]
MARKER_NAMES = ["Dot", "Count", "Fenn badge"]  # dot/count sit between the time and the date
BADGE_HOLD = 10  # Tinted look: same blur, more see-through

notes = []          # newest first: {order, id, aumid, app, icon, image, info, at, unread}
badge_until = 0.0   # monotonic time the pill's unread badge goes away ("10 s" mode)
badge_shown = False
marker_sent = None  # last marker line sent to the bubble process
photo_check = 0.0   # when missing sender photos were last looked for
showing = None      # the note in the bubble
last_check = 0.0


player = wn.SoundPlayer()
SOUNDS_DIR = os.path.join(app.data_dir, "sounds")


def sound_library():
    """[(key, name, path)]: Windows' built-in sounds, then the user's own files."""
    out = [("b:" + i, name, os.path.join(wn.MEDIA, f)) for i, name, f in wn.BUILTIN_SOUNDS
           if os.path.isfile(os.path.join(wn.MEDIA, f))]
    for c in settings["custom_sounds"]:
        if os.path.isfile(c["path"]):
            out.append(("f:" + c["path"], c["name"], c["path"]))
    return out


def sound_name(key):
    if key == "default":
        return "Default"
    if key == "none":
        return "No sound"
    return next((n for k, n, _ in sound_library() if k == key), "Message")


def sound_path(key):
    return next((path for k, _, path in sound_library() if k == key), None)


def sound_for(note):
    """The file to play for a notification, or None for silence."""
    if not settings["sound_on"] or note["info"].get("silent"):
        return None
    key = settings["app_sounds"].get(note["aumid"], "default")
    if key == "none":
        return None
    if key == "default":
        key = settings["sound"]
    return sound_path(key) or sound_path("b:message")


def app_volume(aumid):
    """An app's own volume, or the main one until it gets its own."""
    return settings["app_volumes"].get(aumid, settings["volume"])


def play(path, volume=None):
    if path:
        player.play(path, settings["volume"] if volume is None else volume)


def save_settings():
    app.save("settings", settings)


def ago(ts):
    s = max(0, time.time() - ts)
    if s < 45:
        return "now"
    if s < 3600:
        return f"{int(s // 60)}m"
    return time.strftime("%H:%M", time.localtime(ts))


def list_time(ts):
    """Apple-style: now, 5m, 14:05 today, Yesterday, a weekday this week, else a date."""
    s = max(0, time.time() - ts)
    if s < 45:
        return "now"
    if s < 3600:
        return f"{int(s // 60)}m"
    t, today = time.localtime(ts), time.localtime()
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(t[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    if days < 0.5:
        return time.strftime("%H:%M", t)
    if days < 1.5:
        return "Yesterday"
    if days < 6.5:
        return time.strftime("%A", t)
    return time.strftime("%d %b", t).lstrip("0")


def conv_key(n):
    return (n["aumid"], (n["info"]["title"] or n["app"]).strip().lower())


def conversations():
    """Notifications grouped by app + sender, newest group first, like a
    messages list: [(latest note, all notes in the group)]."""
    groups, order = {}, []
    for n in notes:  # notes are newest first
        key = conv_key(n)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(n)
    return [(groups[k][0], groups[k]) for k in order]


def clip(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# The bubble window


class Bubble:
    """Runs bubble.ps1 and talks to it line by line. Restarts it if it dies."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def _start(self):
        log = open(os.path.join(app.data_dir, "bubble.log"), "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA",
             "-File", os.path.join(HERE, "bubble.ps1")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            cwd=HERE, text=True, encoding="ascii", errors="replace", bufsize=1)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.mtime = self._script_mtime()
        self._send_now(f"theme|{'dark' if app.dark else 'light'}|{app.accent}")
        self._send_now(config_line())
        if layout_sent:
            self._send_now(layout_sent)  # where Fenn's notch is
        if marker_sent:
            self._send_now(marker_sent)  # a restarted bubble process gets the marker back

    @staticmethod
    def _script_mtime():
        try:
            return os.path.getmtime(os.path.join(HERE, "bubble.ps1"))
        except OSError:
            return 0

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if line:
                app._inbox.put({"type": "bubble", "line": line})

    def _send_now(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def send(self, line):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None and self._script_mtime() != self.mtime:
                self._send_now("quit")  # bubble.ps1 was edited: start the new one
                self.proc = None
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            if not self._send_now(line):
                self._start()
                self._send_now(line)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass


bubble = Bubble()


def config_line():
    style = settings["style"] if settings["style"] in STYLES else "solid"
    return (f"config|{style}|{min(4, max(0, int(settings['size'])))}"
            f"|{min(3, max(0, int(settings.get('tint', 0))))}")


# ---------------------------------------------------------------------------
# Following Fenn's own notch settings (position, monitor, margin, shape,
# auto-hide), so the bubble and the unread marker sit on the real pill.

FENN_CONFIG = os.path.join(os.environ.get("APPDATA", ""), "Fenn", "config.toml")
fenn_config_mtime = None
layout_sent = None


def read_fenn_config():
    """Top-level keys of Fenn's config.toml (a tiny parser: Python 3.8 has no
    tomllib, and only plain `key = value` lines before the first table matter)."""
    out = {}
    try:
        with open(FENN_CONFIG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    break
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.split(" #")[0].strip()
                if value[:1] in "\"'":
                    value = value[1:-1]
                elif value in ("true", "false"):
                    value = value == "true"
                else:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
                out[key.strip()] = value
    except OSError:
        pass
    return out


def layout_line():
    cfg = read_fenn_config()
    placement = str(cfg.get("placement", "center")).lower()
    monitor = str(cfg.get("monitor", "primary")).lower().replace("_", "").replace("-", "")
    monitor = "cursor" if "cursor" in monitor or "follow" in monitor else monitor
    try:
        margin = max(0.0, min(64.0, float(cfg.get("margin", 0))))
    except (TypeError, ValueError):
        margin = 0.0
    shape = "notch" if str(cfg.get("shape", "pill")).lower() == "notch" else "pill"
    hide = 1 if cfg.get("auto_hide") is True else 0
    return f"layout|{placement}|{monitor}|{margin:g}|{shape}|{hide}"


def check_fenn_config(force=False):
    """Sends the layout when Fenn's settings change (or on start)."""
    global fenn_config_mtime, layout_sent
    try:
        mtime = os.path.getmtime(FENN_CONFIG)
    except OSError:
        mtime = None
    if not force and mtime == fenn_config_mtime:
        return
    fenn_config_mtime = mtime
    line = layout_line()
    if force or line != layout_sent:
        layout_sent = line
        bubble.send(line)


def b64(s):
    return base64.b64encode((s or "").encode("utf-8")).decode("ascii")


def fullscreen_app_running():
    """True while a game, video or presentation is full screen."""
    state = ctypes.c_int(0)
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except OSError:
        return False
    return state.value in (2, 3, 4)  # busy, D3D full screen, presentation mode


def reply_input(note):
    return next((i for i in note["info"]["inputs"] if i["type"] == "text"), None)


def bubble_buttons(note):
    """The toast's own buttons shown on hover (the reply box has its own send)."""
    limit = 2 if reply_input(note) else 3
    return [a for a in note["info"]["actions"] if a["content"] and not a["input"]][:limit]


def pop(note):
    global showing, badge_until
    showing = note
    badge_until = time.monotonic() + settings["seconds"] + BADGE_HOLD
    others = sum(1 for x in notes if x["unread"] and x is not note)
    info = note["info"]
    body = clip(info["body"] or info["attribution"], 220)
    inp = reply_input(note)
    hint = (inp["hint"] or "Reply") if inp else ""
    labels = chr(0x1F).join(clip(a["content"], 16) for a in bubble_buttons(note))
    bubble.send("|".join([
        "show", str(note["order"]), b64(note["app"]), b64(clip(info["title"], 90)), b64(body),
        b64(note["image"] or note["icon"] or ""), str(others), str(settings["seconds"]), b64(ago(note["at"])),
        b64(hint), b64(labels) if labels else "",
        b64(note["icon"] if note["image"] and note["icon"] else ""),  # small app icon on the sender's photo
    ]))


# ---------------------------------------------------------------------------
# Fenn views: the tile and the page (history + settings)


def avatar(note, size):
    path = note["image"] or note["icon"]
    if path:
        return fenn.image(path=path, width=size, height=size, rounding=size / 2)
    return fenn.icon("bell", size=size * 0.6, color="accent")


def tile_view():
    unread = [n for n in notes if n["unread"]]
    if not notes:
        return fenn.row([
            fenn.icon("bell", size=22, color="dim"),
            fenn.column([
                fenn.text("Notifications", size="small", color="dim", bold=True),
                fenn.text("All caught up", wrap=False),
            ], gap=0),
        ], gap=10, align="center")
    n = unread[0] if unread else notes[0]
    head = n["app"] + (f"  ·  {len(unread)} new" if len(unread) > 1 else f"  ·  {ago(n['at'])}")
    return fenn.row([
        avatar(n, 32),
        fenn.column([
            fenn.text(head, size="small", color="dim", bold=True),
            fenn.text(clip(n["info"]["title"] or n["app"], 60), bold=True, wrap=False),
            fenn.text(clip(n["info"]["body"], 70), color="dim", wrap=False),
        ], gap=0),
    ], gap=10, align="center")


ROW_H = 46          # one conversation row, in points (photo is 40)
LINE_H = 24         # one older message under an expanded row
CHEVRON_W = 46      # right part of a row that expands it instead of opening
expanded = set()    # conversation keys whose older messages are shown
row_width = 400     # canvas width of the rows, for hit-testing the chevron
page_width = 536    # updated from Fenn's layout message


def fit(text, width, size):
    """Cuts text to roughly fit `width` points at font `size` (no measuring
    API in a plugin, so this uses an average glyph width)."""
    text = " ".join((text or "").split())
    chars = max(4, int(width / (size * 0.52)))
    return text if len(text) <= chars else text[: chars - 1].rstrip() + "\u2026"


def conversation_row(latest, group, last):
    """The header row, plus the older messages when it's expanded."""
    many = len(group) > 1
    open_ = many and conv_key(latest) in expanded
    rows = [header_row(latest, group, last or open_, many, open_)]
    if open_:
        older = group[1:11]
        lines = [older_line(n, last_one=(i == len(older) - 1 and not last)) for i, n in enumerate(older)]
        if len(group) > 11:
            lines.append(fenn.row([fenn.canvas(LINE_H, [], width=60),
                                   fenn.canvas(LINE_H, [fenn.label(2, 11, f"+{len(group) - 11} older", size=11.5, color="faint")],
                                               width=row_width)], gap=10))
        rows.append(fenn.column(lines, gap=0))
    return fenn.column(rows, gap=2) if len(rows) > 1 else rows[0]


def older_line(n, last_one):
    """One older message, indented under the name; clicking opens the chat."""
    text = n["info"]["body"] or n["info"]["title"] or n["app"]
    when = list_time(n["at"])
    w = row_width
    shapes = [
        fenn.label(2, 11, fit(text, w - len(when) * 6.5 - 16, 12.5), size=12.5,
                   color="text" if n["unread"] else "dim"),
        fenn.label(w - 2, 11, when, size=11, color="faint", anchor="end"),
    ]
    if last_one:
        shapes.append(fenn.line([[2, LINE_H - 0.5], [w, LINE_H - 0.5]], color="faint", width=0.5))
    # 60 + the row gap lines the text up with the name above (dot, photo, gaps).
    return fenn.row([fenn.canvas(LINE_H, [], width=60),
                     fenn.canvas(LINE_H, shapes, width=w, id=f"n:{n['order']}",
                                 tooltip="Open in " + n["app"])], gap=10)


def header_row(latest, group, last, many, open_):
    """One messages-list row: unread dot, round photo, then a canvas with the
    name, latest message and time. A canvas has a fixed height (a list would
    stretch and push the photo out of line) and its clicks open the chat."""
    unread = sum(1 for n in group if n["unread"])
    name = latest["info"]["title"] or latest["app"]
    message = latest["info"]["body"] or latest["info"]["attribution"]
    if unread > 1:
        message = f"{unread} new  \u00b7  {message}"
    when = list_time(latest["at"])
    # With a sender photo the app isn't visible any more, so name it by the time.
    trailing = f"{latest['app']}  \u00b7  {when}" if latest["image"] else when

    global row_width
    w = max(160, page_width - 9 - 40 - 2 * 10 - 14)  # dot, photo, gaps, scrollbar
    row_width = w
    time_w = len(trailing) * 11.5 * 0.55 + 12
    msg_w = w - 8 - (CHEVRON_W if many else 0)
    shapes = [
        # Rows are top-aligned (a centred row in Fenn takes all the height it
        # can get), so the text is placed to sit level with the 40 pt photo.
        fenn.label(2, 11, fit(name, w - time_w - 6, 14.5), size=14.5, bold=True, color="text"),
        fenn.label(w - 2, 11, trailing, size=11.5, color="accent" if unread else "faint", anchor="end"),
        fenn.label(2, 30, fit(message, msg_w, 12.5), size=12.5, color="dim"),
    ]
    if many:
        # How many, and a chevron that opens the older ones (clicks here don't open the chat).
        shapes.append(fenn.label(w - 22, 30, str(len(group)), size=11.5, color="faint", anchor="end"))
        shapes.append(fenn.glyph(w - 10, 30, "caret-up" if open_ else "caret-down", size=13, color="dim"))
    if not last:
        shapes.append(fenn.line([[2, ROW_H - 0.5], [w, ROW_H - 0.5]], color="faint", width=0.5))
    # A fixed 10x10 canvas, empty when read: a spacer here grows tall in Fenn's
    # rows and pushes the whole row down.
    dot = fenn.canvas(40, [fenn.circle(5, 20, 4.5, fill="accent")] if unread else [], width=10)
    return fenn.row([
        dot,
        avatar(latest, 40),
        fenn.canvas(ROW_H, shapes, width=w, id=f"n:{latest['order']}",
                    tooltip=("Open in " + latest["app"]) + ("  \u00b7  arrow shows older messages" if many else "")),
    ], gap=10)


def page_view():
    parts = []
    if notes:
        convs = conversations()[:30]
        for i, (latest, group) in enumerate(convs):
            parts.append(conversation_row(latest, group, i == len(convs) - 1))
    else:
        parts.append(fenn.column([
            fenn.icon("bell", size=28, color="faint"),
            fenn.text("No notifications", bold=True),
            fenn.text("You're all caught up.", color="faint"),
        ], gap=4, align="center"))

    parts += [
        fenn.divider(),
        fenn.toggle("popup", settings["popup"], label="Pop out of the island when one arrives"),
        fenn.row([
            fenn.text("Collapse after", color="dim"),
            fenn.chips("seconds", [f"{d}s" for d in DURATIONS],
                       DURATIONS.index(settings["seconds"]) if settings["seconds"] in DURATIONS else 1),
        ], gap=8),
        fenn.row([
            fenn.text("Look", color="dim"),
            fenn.chips("style", STYLE_NAMES, STYLES.index(settings["style"]) if settings["style"] in STYLES else 0),
        ], gap=8),
    ]
    if settings["style"] == "tinted":
        parts.append(fenn.row([
            fenn.text("Tint", color="dim"),
            fenn.chips("tint", TINT_NAMES, min(3, max(0, int(settings.get("tint", 0))))),
        ], gap=8))
    parts += [
        fenn.row([
            fenn.text("Size", color="dim"),
            fenn.chips("size", SIZE_NAMES, min(4, max(0, int(settings["size"])))),
        ], gap=8),
        fenn.row([
            fenn.text("Unread marker", color="dim"),
            fenn.chips("marker", MARKER_NAMES, min(2, max(0, int(settings.get("marker", 1))))),
        ], gap=8),
        fenn.row([
            fenn.text("Show it", color="dim"),
            fenn.chips("pill_badge", BADGE_NAMES, min(2, max(0, int(settings.get("pill_badge", 1))))),
        ], gap=8),
        fenn.toggle("photos", settings["photos"], label="Sender photos (Beeper: from its local contact list)"),
        fenn.toggle("fullscreen", settings["fullscreen"], label="Also pop out over full-screen apps and games"),
        fenn.toggle("hide_banners", settings["hide_banners"],
                    label="Hide Windows banners (show them only here)"),
        fenn.text("Turns off “Show notification banners” for each app in Windows; they still go to "
                  "the notification center. Switching this off puts the banners back. If an app keeps "
                  "showing banners, sign out and in once, or turn on Do not disturb.",
                  size="small", color="faint", wrap=True),
        fenn.divider(),
        fenn.toggle("sound_on", settings["sound_on"], label="Play a sound with each notification"),
        fenn.row([
            fenn.text("Sound", color="dim"),
            fenn.button("sound_next", label=sound_name(settings["sound"]), icon="music-note",
                        tooltip="Click for the next sound"),
            fenn.button("sound_test", icon="play", tooltip="Play it"),
        ], gap=8),
        fenn.row([
            fenn.text("Volume", color="dim"),
            fenn.slider("volume", settings["volume"], min=0, max=100, step=5),
        ], gap=8),
        fenn.text("Drop an MP3 or WAV file on this page to add your own sound.",
                  size="small", color="faint", wrap=True),
        fenn.toggle("mute_windows_sounds", settings["mute_windows_sounds"],
                    label="Mute Windows\u2019 own notification sounds"),
        fenn.text("Sets the Notification sounds in Control Panel \u203a Sound to (None); alarms and calls "
                  "keep ringing. Switching this off puts the old sounds back.",
                  size="small", color="faint", wrap=True),
    ]
    apps = {n["aumid"]: n["app"] for n in notes}
    for aumid in settings["muted"]:
        apps.setdefault(aumid, wn.app_name(aumid, feed))
    if apps:
        parts.append(fenn.text("Apps", size="small", color="dim", bold=True))
        for aumid, name in sorted(apps.items(), key=lambda kv: kv[1].lower()):
            parts.append(fenn.row([
                fenn.toggle(f"app:{aumid}", aumid not in settings["muted"], label=name),
                fenn.button(f"snd:{aumid}", label=sound_name(settings["app_sounds"].get(aumid, "default")),
                            icon="speaker-high", tooltip="Sound for this app: click for the next one"),
                fenn.slider(f"vol:{aumid}", app_volume(aumid), min=0, max=100, step=5, width=90,
                            tooltip="Volume for this app"),
            ], gap=10))
    return fenn.column(parts, gap=8, scroll=True)


def draw():
    app.tile(tile_view())
    app.page(page_view(), actions=[{"id": "clear", "icon": "broom", "tooltip": "Clear all"}])
    global badge_shown, marker_sent
    unread = sum(1 for n in notes if n["unread"])
    mode = settings.get("pill_badge", 1)
    show = bool(unread) and (mode == 2 or (mode == 1 and time.monotonic() < badge_until))
    style = MARKERS[min(2, max(0, int(settings.get("marker", 1))))]
    if show and style == "fenn":
        app.badge_ring(1.0, str(unread) if unread < 100 else "99+")
    else:
        app.clear_badge()
    line = f"marker|{style if show and style != 'fenn' else 'none'}|{unread}"
    if line != marker_sent:
        marker_sent = line
        bubble.send(line)
    badge_shown = show


# ---------------------------------------------------------------------------
# Incoming


def ingest(raw):
    aumid = raw["aumid"]
    info = wn.parse_toast(raw["payload"])
    if not (info["title"] or info["body"]):
        return None
    at = wn.filetime_to_unix(raw["arrival"]) if raw["arrival"] else time.time()
    if at > time.time() + 60 or at < time.time() - 86400:
        at = time.time()
    if settings["hide_banners"]:
        hide_banner_for(aumid)
    return {
        "order": raw["order"], "id": raw["id"], "aumid": aumid, "tag": raw["tag"],
        "app": wn.app_name(aumid, feed),
        "icon": wn.app_icon(aumid, cache, feed),
        "image": wn.sender_photo(aumid, info, cache) if settings["photos"] else wn.local_image(info["image"], cache),
        "info": info, "at": at, "unread": True,
    }


def poll():
    global last_check, badge_until, photo_check
    if badge_shown and settings.get("pill_badge", 1) == 1 and time.monotonic() >= badge_until:
        draw()  # the 10 s are up: the pill goes back to Fenn's own view
    fresh = []
    for raw in feed.poll():
        if raw["aumid"] in settings["muted"]:
            continue
        note = ingest(raw)
        if note:
            fresh.append(note)
    if fresh:
        for n in fresh:
            notes.insert(0, n)
        del notes[60:]
        badge_until = time.monotonic() + BADGE_HOLD
        quiet = not settings["fullscreen"] and fullscreen_app_running()
        if not quiet:
            loud = next((n for n in reversed(fresh) if sound_for(n)), None)
            if loud:
                play(sound_for(loud), app_volume(loud["aumid"]))
        if settings["popup"] and (settings["fullscreen"] or not fullscreen_app_running()):
            pop(fresh[-1])  # also pushes the badge timer past the bubble's own time
        draw()

    # Photos that weren't there yet (Beeper downloads a new contact's avatar
    # once it shows the chat): look again now and then and fill them in.
    now = time.monotonic()
    if settings["photos"] and now - photo_check > 30:
        photo_check = now
        found = False
        for n in notes[:40]:
            if not n["image"]:
                n["image"] = wn.sender_photo(n["aumid"], n["info"], cache)
                found = found or bool(n["image"])
        if found:
            draw()

    player.cleanup()

    # Toasts dismissed in Windows (or retracted by the app) leave here too.
    if now - last_check > 3:
        last_check = now
        gone = [n for n in notes if n["unread"] and not feed.still_present(n["id"])]
        if gone:
            for n in gone:
                n["unread"] = False
            if showing in gone:
                bubble.send("hide")
            draw()


def find(order):
    try:
        order = int(order)
    except ValueError:
        return None
    return next((x for x in notes if x["order"] == order), None)


def run_action(n, action, inputs=None):
    n["unread"] = False
    wn.activate(n["aumid"], action["arguments"], action["activation"], inputs or {}, log=app.log)
    draw()


def open_note(n):
    """Does what clicking the Windows notification would do. Apps like Beeper
    find the chat through the live notification (args carry its tag) and
    remove their notifications once the chat is read, so an older message's
    own args can be stale: then use the newest notification of the same
    conversation that Windows still has, or just bring the app up."""
    n["unread"] = False
    args = wn.click_arguments(n)
    target = n
    if "tag=" in args and not feed.still_present(n["id"]):
        key = conv_key(n)
        target = next((x for x in notes if conv_key(x) == key and feed.still_present(x["id"])), None)
    if target is not None:
        wn.activate(target["aumid"], wn.click_arguments(target), target["info"]["activation"], log=app.log)
    else:
        wn.activate(n["aumid"], "", "foreground", log=app.log, launch_only=True)
    draw()


@app.on_message("bubble")
def on_bubble(msg):
    global showing, badge_until
    kind, _, arg = msg["line"].partition("|")
    if kind == "click":
        n = find(arg)
        if n:
            open_note(n)
    elif kind == "dismiss":
        n = find(arg)
        if n:
            n["unread"] = False
            draw()
    elif kind == "action":
        oid, _, idx = arg.partition("|")
        n = find(oid)
        if n:
            buttons = bubble_buttons(n)
            if idx.isdigit() and int(idx) < len(buttons):
                run_action(n, buttons[int(idx)])
    elif kind == "reply":
        oid, _, text = arg.partition("|")
        n = find(oid)
        inp = reply_input(n) if n else None
        if n and inp:
            text = base64.b64decode(text).decode("utf-8", "replace")
            acts = n["info"]["actions"]
            act = (next((a for a in acts if a["input"] == inp["id"]), None)
                   or next((a for a in acts if "reply" in (a["arguments"] + a["content"]).lower()), None))
            if act:
                run_action(n, act, {inp["id"]: text})
            else:
                app.log(f"{n['app']}: no send action for the reply box", "warn")
    elif kind == "hidden":
        badge_until = time.monotonic() + BADGE_HOLD  # count from when the bubble went back into the pill
        showing = None


# ---------------------------------------------------------------------------
# Page controls


@app.on("clear")
def on_clear(e):
    notes.clear()
    draw()


@app.on("popup")
def on_popup(e):
    settings["popup"] = bool(e.get("value"))
    save_settings()
    draw()


@app.on("style")
def on_style(e):
    settings["style"] = STYLES[e["value"]["index"]]
    save_settings()
    bubble.send(config_line())
    draw()


@app.on("tint")
def on_tint(e):
    settings["tint"] = e["value"]["index"]
    save_settings()
    bubble.send(config_line())
    draw()


@app.on("size")
def on_size(e):
    settings["size"] = e["value"]["index"]
    save_settings()
    bubble.send(config_line())
    draw()


@app.on("marker")
def on_marker(e):
    settings["marker"] = e["value"]["index"]
    save_settings()
    draw()


@app.on("pill_badge")
def on_pill_badge(e):
    settings["pill_badge"] = e["value"]["index"]
    save_settings()
    draw()


@app.on("photos")
def on_photos(e):
    settings["photos"] = bool(e.get("value"))
    save_settings()
    draw()


@app.on("sound_on")
def on_sound_on(e):
    settings["sound_on"] = bool(e.get("value"))
    save_settings()
    if settings["sound_on"]:
        play(sound_path(settings["sound"]))
    draw()


@app.on("sound_next")
def on_sound_next(e):
    keys = [k for k, _, _ in sound_library()]
    i = keys.index(settings["sound"]) if settings["sound"] in keys else -1
    settings["sound"] = keys[(i + 1) % len(keys)]
    save_settings()
    play(sound_path(settings["sound"]))
    draw()


@app.on("sound_test")
def on_sound_test(e):
    play(sound_path(settings["sound"]))


@app.on("volume")
def on_volume(e):
    settings["volume"] = int(e.get("value") or 0)
    if e.get("kind") == "commit":
        save_settings()
        play(sound_path(settings["sound"]))


@app.on("mute_windows_sounds")
def on_mute_windows_sounds(e):
    settings["mute_windows_sounds"] = bool(e.get("value"))
    settings["saved_sounds"] = wn.mute_windows_sounds(settings["mute_windows_sounds"], settings["saved_sounds"])
    save_settings()
    draw()


@app.on_message("files")
def on_files(msg):
    """Dropped sound files become choices; the last one is the new default."""
    import shutil
    added = None
    for path in msg.get("paths", []):
        if not path.lower().endswith(wn.SOUND_FILE_TYPES) or not os.path.isfile(path):
            continue
        os.makedirs(SOUNDS_DIR, exist_ok=True)
        dst = os.path.join(SOUNDS_DIR, os.path.basename(path))
        if os.path.abspath(path) != os.path.abspath(dst):
            shutil.copyfile(path, dst)
        name = os.path.splitext(os.path.basename(path))[0][:24]
        settings["custom_sounds"] = [c for c in settings["custom_sounds"] if c["path"] != dst]
        settings["custom_sounds"].append({"name": name, "path": dst})
        added = "f:" + dst
    if added:
        settings["sound"] = added
        save_settings()
        play(sound_path(added))
        draw()


@app.on("fullscreen")
def on_fullscreen(e):
    settings["fullscreen"] = bool(e.get("value"))
    save_settings()
    draw()


@app.on("seconds")
def on_seconds(e):
    settings["seconds"] = DURATIONS[e["value"]["index"]]
    save_settings()
    draw()


@app.on("hide_banners")
def on_hide_banners(e):
    settings["hide_banners"] = bool(e.get("value"))
    if settings["hide_banners"]:
        for aumid in wn.known_apps():
            hide_banner_for(aumid)
    else:
        for aumid in settings["banner_changed"]:
            wn.set_banner(aumid, True)
        settings["banner_changed"] = []
    save_settings()
    draw()


def hide_banner_for(aumid):
    if aumid in settings["banner_changed"] or wn.banner_state(aumid) == 0:
        return
    if wn.set_banner(aumid, False):
        settings["banner_changed"].append(aumid)
        save_settings()


@app.on_message("event")
def on_event(e):
    eid = e.get("id", "")
    if eid.startswith("n:"):
        n = find(eid[2:])
        value = e.get("value") if isinstance(e.get("value"), dict) else e
        x = value.get("x") if isinstance(value, dict) else None
        convs = conversations()
        header = next(((l, g) for l, g in convs if l is n), None)
        if n and header and len(header[1]) > 1 and x is not None and x >= row_width - CHEVRON_W:
            key = conv_key(n)  # the chevron: show or hide the older messages
            expanded.symmetric_difference_update({key})
            draw()
            return
        if n:
            for latest, group in convs:
                if n in group:
                    for m in group:
                        m["unread"] = False
            open_note(n)
            app.close_page()
    elif eid.startswith("snd:"):
        # Per-app sound: Default -> No sound -> each sound in the library -> Default ...
        aumid = eid[4:]
        keys = ["default", "none"] + [k for k, _, _ in sound_library()]
        cur = settings["app_sounds"].get(aumid, "default")
        nxt = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else "none"
        if nxt == "default":
            settings["app_sounds"].pop(aumid, None)
        else:
            settings["app_sounds"][aumid] = nxt
        save_settings()
        play(sound_path(nxt) if nxt not in ("default", "none") else
             sound_path(settings["sound"]) if nxt == "default" else None, app_volume(aumid))
        draw()
    elif eid.startswith("vol:"):
        aumid = eid[4:]
        settings["app_volumes"][aumid] = int(e.get("value") or 0)
        if e.get("kind") == "commit":
            save_settings()
            key = settings["app_sounds"].get(aumid, "default")
            if key != "none":
                play(sound_path(settings["sound"] if key == "default" else key), app_volume(aumid))
    elif eid.startswith("app:"):
        aumid = eid[4:]
        muted = settings["muted"]
        if e.get("value"):
            if aumid in muted:
                muted.remove(aumid)
        elif aumid not in muted:
            muted.append(aumid)
        save_settings()
        draw()


@app.on_message("page_open")
def on_page_open(msg):
    draw()  # unread dots stay visible while you look


@app.on_message("page_close")
def on_page_close(msg):
    # Seen now: clear the dots (and the pill marker) once the page closes.
    if any(n["unread"] for n in notes):
        for n in notes:
            n["unread"] = False
        draw()


@app.on_message("layout")
def on_layout(msg):
    global page_width
    if msg.get("target") == "page" and msg.get("width") and abs(msg["width"] - page_width) > 1:
        page_width = msg["width"]
        draw()


@app.on_message("theme")
def on_theme(msg):
    bubble.send(f"theme|{'dark' if app.dark else 'light'}|{app.accent}")
    draw()


@app.on_message("hello")
def on_hello(msg):
    bubble.send(f"theme|{'dark' if app.dark else 'light'}|{app.accent}")


@app.on_message("shutdown")
def on_shutdown(msg):
    bubble.stop()


threading.Thread(target=wn._shortcut_activators, daemon=True).start()  # warm the click path
threading.Thread(target=lambda: bubble.send("hide"), daemon=True).start()  # start the window early

app.every(0.35, poll)
app.every(1.0, check_fenn_config)  # Fenn's settings: follow moves, margin and shape changes
check_fenn_config(force=True)
app.every(30, draw)  # keeps "2m ago" labels fresh
poll()  # sets the starting point; old toasts are not replayed
draw()
try:
    app.run()
finally:
    bubble.stop()
