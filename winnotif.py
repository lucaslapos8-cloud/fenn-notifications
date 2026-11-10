"""Windows side of the Notifications plugin. Standard library only (ctypes,
sqlite3, winreg), so it runs on a plain Python install.

- NotificationFeed: reads new toasts from the Windows notification database
  (the same store the notification center shows).
- parse_toast: turns toast XML into title / body / image / actions.
- app_info: display name and a PNG icon for an AppUserModelID.
- activate: does what clicking the toast would do (launch args through the
  app's COM activator, a protocol URI, or a packaged-app launch).
- set_banner: per-app "Show notification banners" (registry).
"""

import ctypes
import ctypes.wintypes as wt
import os
import json
import re
import sqlite3
import struct
import threading
import time
import urllib.parse
import urllib.request
import winreg
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
import zlib

DB_PATH = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")
SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"

# ---------------------------------------------------------------------------
# Reading notifications


class NotificationFeed:
    """Polls wpndatabase.db for toasts that arrived since the last poll.

    The table's "Order" column is a plain rowid: once notifications are
    cleared, Windows hands out the same numbers again, so it can't tell new
    from old. Arrival time plus the notification Id (which only grows) can."""

    WINDOW = 60 * 10_000_000  # look back 60 s (FILETIME ticks) in case of clock nudges

    def __init__(self):
        self.conn = None
        self.since = None        # newest ArrivalTime seen
        self.max_id = 0          # newest notification Id seen
        self.seen = set()        # (Id, ArrivalTime) already handed out

    def _connect(self):
        if self.conn is None:
            self.conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        return self.conn

    def poll(self):
        """New toasts as dicts: order (a unique key), id, aumid, tag, group,
        arrival, payload."""
        try:
            c = self._connect()
            if self.since is None:
                rows = c.execute("select Id, ArrivalTime from Notification").fetchall()
                self.seen = {(i, a) for i, a in rows}
                self.since = max((a or 0 for _, a in rows), default=0)
                self.max_id = max((i or 0 for i, _ in rows), default=0)
                return []
            rows = c.execute(
                'select n.Id, h.PrimaryId, n.Tag, n."Group", n.ArrivalTime, n.Payload '
                'from Notification n join NotificationHandler h on h.RecordId = n.HandlerId '
                "where n.Type = 'toast' and (n.ArrivalTime >= ? or n.Id > ?) order by n.ArrivalTime",
                (self.since - self.WINDOW, self.max_id),
            ).fetchall()
        except sqlite3.Error:
            # Busy or recreated by Windows; reconnect next time.
            try:
                if self.conn:
                    self.conn.close()
            finally:
                self.conn = None
            return []
        out = []
        for nid, aumid, tag, group, arrival, payload in rows:
            key = (nid, arrival)
            if key in self.seen:
                continue
            self.seen.add(key)
            self.since = max(self.since, arrival or 0)
            self.max_id = max(self.max_id, nid or 0)
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", "replace")
            payload = self.fill_data(nid, payload or "")
            out.append({"order": nid, "id": nid, "aumid": aumid, "tag": tag or "",
                        "group": group or "", "arrival": arrival, "payload": payload or ""})
        if len(self.seen) > 2000:  # keep only what the look-back window can return
            cutoff = self.since - self.WINDOW * 2
            self.seen = {k for k in self.seen if (k[1] or 0) >= cutoff or (k[0] or 0) >= self.max_id - 50}
        return out

    def fill_data(self, nid, payload):
        """Fills a toast's data-binding placeholders ({contactName}, as in
        Phone Link's call toasts) with the values Windows keeps for it in
        NotificationData. Placeholders without a value are removed."""
        if "{" not in payload:
            return payload
        try:
            data = dict(self._connect().execute(
                "select Key, Value from NotificationData where NotificationId = ?", (nid,)).fetchall())
        except sqlite3.Error:
            data = {}

        def value(m):
            v = data.get(m.group(1))
            return xml_escape(str(v), {'"': "&quot;"}) if v is not None else ""
        return re.sub(r"\{([A-Za-z_][\w.]*)\}", value, payload)

    def apps_with_toasts(self):
        """AppUserModelIDs that have notifications in the notification center now."""
        try:
            return [r[0] for r in self._connect().execute(
                "select distinct h.PrimaryId from Notification n join NotificationHandler h "
                "on h.RecordId = n.HandlerId where n.Type = 'toast'")]
        except sqlite3.Error:
            return []

    def still_present(self, nid):
        """False once the toast was dismissed in Windows (or by the app)."""
        try:
            row = self._connect().execute("select 1 from Notification where Id = ?", (nid,)).fetchone()
            return row is not None
        except sqlite3.Error:
            return True

    def handler_asset(self, aumid, key):
        try:
            row = self._connect().execute(
                "select a.AssetValue from HandlerAssets a join NotificationHandler h "
                "on h.RecordId = a.HandlerId where h.PrimaryId = ? and a.AssetKey = ?",
                (aumid, key)).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None


def parse_toast(payload):
    """Toast XML -> dict(title, body, attribution, image, launch, activation,
    actions, menu (right-click items), inputs, silent). Unknown shapes degrade
    to plain text."""
    info = {"title": "", "body": "", "attribution": "", "image": None, "hero": None,
            "launch": "", "activation": "foreground", "actions": [], "menu": [], "inputs": [],
            "silent": False, "scenario": ""}
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        info["body"] = re.sub(r"<[^>]+>", " ", payload).strip()[:300]
        return info
    info["launch"] = root.get("launch", "") or ""
    info["activation"] = (root.get("activationType") or "foreground").lower()
    info["scenario"] = root.get("scenario", "") or ""
    binding = None
    for b in root.iter("binding"):
        if binding is None or b.get("template") == "ToastGeneric":
            binding = b
    texts = []
    if binding is not None:
        for el in binding:
            if el.tag == "text":
                if el.get("placement") == "attribution":
                    info["attribution"] = (el.text or "").strip()
                elif (el.text or "").strip():
                    texts.append(el.text.strip())
            elif el.tag == "image":
                src = el.get("src", "")
                place = el.get("placement", "")
                if (place == "appLogoOverride" or el.get("hint-crop") == "circle") and not info["image"]:
                    info["image"] = src
                elif place == "hero" or not place:
                    info["hero"] = info["hero"] or src
            elif el.tag == "group":  # adaptive subgroups
                for t in el.iter("text"):
                    if (t.text or "").strip():
                        texts.append(t.text.strip())
    if texts:
        info["title"] = texts[0]
        info["body"] = "\n".join(texts[1:])
    audio = root.find("audio")
    if audio is not None and audio.get("silent") == "true":
        info["silent"] = True
    info["audio"] = audio.get("src", "") if audio is not None else ""
    actions = root.find("actions")
    if actions is not None:
        for inp in actions.findall("input"):
            info["inputs"].append({"id": inp.get("id", ""), "type": inp.get("type", "text"),
                                   "hint": inp.get("placeHolderContent", "")})
        for act in actions.findall("action"):
            # Right-click menu items (WhatsApp's "Mark as read") go in their own list.
            into = info["menu"] if act.get("placement") == "contextMenu" else info["actions"]
            into.append({
                "content": act.get("content", ""),
                "arguments": act.get("arguments", ""),
                "activation": (act.get("activationType") or "foreground").lower(),
                "input": act.get("hint-inputId", ""),
            })
    return info


CALL_WORDS = re.compile(r"\bincoming\s+(?:voice\s+|video\s+|audio\s+|group\s+)?call\b|\bis\s+calling\b"
                        r"|\bcalling\s+you\b|\bvideo\s+call\b|\bvoice\s+call\b", re.I)
CALL_ACTIONS = re.compile(r"(?:accept|answer|decline|reject|ignore)[\w-]*call|call[\w-]*(?:accept|answer|decline|reject)"
                          r"|incomingcall", re.I)


def is_call(info, tag="", group=""):
    """Whether a toast is a phone or app call, from any app: the toast's
    call scenario or ringtone, call buttons (accept / decline), a call tag,
    or words like "Incoming call" / "is calling"."""
    if info.get("scenario", "").lower() == "incomingcall":
        return True
    if "looping.call" in info.get("audio", "").lower():
        return True
    args = " ".join([info.get("launch", "")] + [a["arguments"] + " " + a["content"] for a in info["actions"]])
    if CALL_ACTIONS.search(args):
        return True
    if re.search(r"call", f"{tag} {group}", re.I) and info["actions"]:
        return True
    return bool(CALL_WORDS.search(f"{info.get('title', '')} {info.get('body', '')}"))


def click_arguments(note):
    """Arguments for a click on the toast body. Toasts without a launch
    string but with Electron-style action args (type=...&tag=X) get the
    matching click form so the app can find the conversation."""
    info = note["info"]
    if info["launch"]:
        return info["launch"]
    for act in info["actions"]:
        m = re.search(r"(?:^|&)tag=([^&]+)", act["arguments"])
        if m and "type=" in act["arguments"]:
            return f"type=click&tag={m.group(1)}"
    return ""


PACKAGE_FOLDERS = {"local": "LocalState", "roaming": "RoamingState", "temp": "TempState",
                   "localcache": "LocalCache"}


def local_image(src, cache_dir, aumid=None):
    """A local file path for a toast image src, or None. Handles file paths,
    web addresses (downloaded once) and ms-appdata:/// links, which packaged
    apps (WhatsApp, Phone Link, ...) use for pictures in their own data folder."""
    if not src:
        return None
    try:
        if src.lower().startswith("ms-appdata:///") and aumid and "!" in aumid:
            rest = urllib.parse.unquote(src[len("ms-appdata:///"):])
            folder, _, sub = rest.partition("/")
            base = PACKAGE_FOLDERS.get(folder.lower())
            if base:
                path = os.path.join(os.path.expandvars(r"%LOCALAPPDATA%\Packages"), aumid.split("!")[0],
                                    base, *[p for p in sub.split("/") if p and p != ".."])
                return path if os.path.isfile(path) else None
            return None
        if src.startswith("file:"):
            path = urllib.request.url2pathname(urllib.parse.urlparse(src).path)
            if re.match(r"^\\[A-Za-z]:", path):
                path = path[1:]
            return path if os.path.isfile(path) else None
        if re.match(r"^[A-Za-z]:\\", src):
            return src if os.path.isfile(src) else None
        if src.startswith(("http://", "https://")):
            name = "img-" + format(zlib.crc32(src.encode()), "08x")
            path = os.path.join(cache_dir, name)
            if not os.path.isfile(path):
                with urllib.request.urlopen(src, timeout=4) as r:
                    data = r.read(4 * 1024 * 1024)
                with open(path, "wb") as f:
                    f.write(data)
            return path
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Sender photos for apps whose toasts carry none

BEEPER_DIR = os.path.expandvars(r"%APPDATA%\BeeperTexts")
BEEPER_AUMIDS = ("com.automattic.beeper.desktop", "com.automattic.beeper")
_photo_cache = {}  # (aumid, name) -> (path or None, time looked up)


def _image_ext(path):
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return None
    if head[:2] == bytes((0xFF, 0xD8)):
        return ".jpg"
    if head[:4] == bytes((0x89,)) + b"PNG":
        return ".png"
    if head.startswith(b"GIF8"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None  # e.g. animated avatars stored as video


def _cached_copy(src, cache_dir, px=64):
    """A small square PNG of an image, in the plugin cache. Fenn lays an image
    out at its real pixel size, so a 200 px avatar would stretch a 40 pt row;
    Windows' own thumbnailer shrinks it (and takes files without an extension,
    like Beeper's .avatar, once they are copied under the right one)."""
    ext = _image_ext(src)
    if not ext:
        return None
    dst = os.path.join(cache_dir, f"av-{zlib.crc32(src.encode()):08x}-{px}.png")
    try:
        if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            return dst
        os.makedirs(cache_dir, exist_ok=True)
        named = src
        if not src.lower().endswith(ext):
            named = os.path.join(cache_dir, f"src-{zlib.crc32(src.encode()):08x}{ext}")
            with open(src, "rb") as a, open(named, "wb") as b:
                b.write(a.read(8 * 1024 * 1024))
        data = _shell_png(os.path.abspath(named), px, 0x8)  # THUMBNAILONLY, fitted to px
        if named != src:
            try:
                os.remove(named)
            except OSError:
                pass
        if not data:
            return None
        with open(dst + ".tmp", "wb") as f:
            f.write(data)
        os.replace(dst + ".tmp", dst)
        return dst
    except OSError:
        return None


def _mxc_path(url):
    m = re.match(r"mxc://([^/]+)/([^?#/]+)", url or "")
    if not m:
        return None
    return os.path.join(BEEPER_DIR, "media", m.group(1), m.group(2) + ".avatar")


def _beeper_photo(name, cache_dir):
    db = os.path.join(BEEPER_DIR, "index.db")
    if not os.path.isfile(db):
        return None
    names = [name.strip()]
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", names[0]).strip()  # "Name (Instagram)" -> "Name"
    if bare and bare != names[0]:
        names.append(bare)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return None
    try:
        for n in names:
            rows = conn.execute(
                "select img_url from participants where img_url like 'mxc://%' and "
                "(full_name = ? collate nocase or nickname = ? collate nocase)", (n, n)).fetchall()
            # Group chats: the toast title is the chat's name, which has its own picture.
            rows += conn.execute(
                "select json_extract(thread, '$.imgURL') from threads where "
                "json_extract(thread, '$.title') = ? collate nocase and "
                "json_extract(thread, '$.imgURL') like 'mxc://%'", (n,)).fetchall()
            for (url,) in rows:
                src = _mxc_path(url)
                if src and os.path.isfile(src):
                    path = _cached_copy(src, cache_dir)
                    if path:
                        return path
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return None


def sender_photo(aumid, info, cache_dir):
    """The sender's picture: from the toast itself, or for Beeper from its
    local contact list (index.db, read-only) and avatar cache. None if unknown."""
    path = local_image(info.get("image"), cache_dir, aumid)
    if path:
        return _cached_copy(path, cache_dir)
    if aumid.lower() not in BEEPER_AUMIDS or not info.get("title"):
        return None
    key = (aumid, info["title"].strip().lower())
    hit = _photo_cache.get(key)
    if hit and (hit[0] or time.time() - hit[1] < 30):  # retry misses: Beeper downloads avatars when it shows them
        return hit[0]
    path = _beeper_photo(info["title"], cache_dir)
    _photo_cache[key] = (path, time.time())
    return path


# ---------------------------------------------------------------------------
# COM plumbing (ctypes only)

ole32 = ctypes.OleDLL("ole32")
shell32 = ctypes.OleDLL("shell32")
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")

CLSCTX_LOCAL_SERVER = 0x4
CLSCTX_ALL = 0x17


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("ptr", ctypes.c_void_p), ("extra", ctypes.c_void_p)]


class NOTIFICATION_USER_INPUT_DATA(ctypes.Structure):
    _fields_ = [("Key", wt.LPCWSTR), ("Value", wt.LPCWSTR)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


def guid(s):
    g = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g


def guid_str(g):
    buf = ctypes.create_unicode_buffer(64)
    ole32.StringFromGUID2(ctypes.byref(g), buf, 64)
    return buf.value


def pkey(fmtid, pid):
    return PROPERTYKEY(guid(fmtid), pid)


PKEY_ItemNameDisplay = pkey("{B725F130-47EF-101A-A5F1-02608C9EEBAC}", 10)
PKEY_ToastActivatorCLSID = pkey("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 26)
IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
IID_IShellItemImageFactory = "{BCC18B79-BA16-442F-80C4-8A59C30C463B}"
IID_INotificationActivationCallback = "{53E31837-6600-4A81-9395-75CFFE746F94}"
CLSID_ApplicationActivationManager = "{45BA127D-10A8-46EA-8AB7-56EA9078943C}"
IID_IApplicationActivationManager = "{2E941141-7F97-4756-BA1D-9DECDE894A3D}"


def method(ptr, index, argtypes, restype=ctypes.HRESULT):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def release(ptr):
    if ptr:
        method(ptr, 2, [], ctypes.c_ulong)(ptr)


_com = threading.local()


def com_init():
    if getattr(_com, "ready", False):
        return
    try:
        ole32.CoInitializeEx(None, 0x2)  # apartment threaded
    except OSError:
        pass  # already initialised in another mode
    _com.ready = True


def apps_folder(aumid):
    return "shell:AppsFolder\\" + aumid


def _property(aumid, key, path=None):
    """A string or CLSID property of the Start menu entry for aumid (or of
    the file at path, e.g. a shortcut)."""
    com_init()
    store = ctypes.c_void_p()
    try:
        shell32.SHGetPropertyStoreFromParsingName(
            ctypes.c_wchar_p(path or apps_folder(aumid)), None, 0,
            ctypes.byref(guid(IID_IPropertyStore)), ctypes.byref(store))
    except OSError:
        return None
    try:
        pv = PROPVARIANT()
        method(store, 5, [ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)])(
            store, ctypes.byref(key), ctypes.byref(pv))
        try:
            if pv.vt == 31 and pv.ptr:  # VT_LPWSTR
                return ctypes.wstring_at(pv.ptr)
            if pv.vt == 72 and pv.ptr:  # VT_CLSID
                return guid_str(GUID.from_address(pv.ptr))
            return None
        finally:
            ole32.PropVariantClear(ctypes.byref(pv))
    except OSError:
        return None
    finally:
        release(store)


def _reg_value(root, path, name):
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


_activators = {}
_shortcut_map = None
_shortcut_lock = threading.Lock()
PKEY_AppUserModelID = pkey("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 5)


def _shortcut_activators():
    """{aumid (lower): clsid} from Start menu shortcuts, which is where
    desktop apps (Electron, Chromium, ...) register their activator."""
    global _shortcut_map
    with _shortcut_lock:
        if _shortcut_map is None:
            _shortcut_map = _scan_shortcuts()
        return _shortcut_map


def _scan_shortcuts():
    found = {}
    roots = [os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
             os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs")]
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue
                path = os.path.join(dirpath, f)
                clsid = _property(None, PKEY_ToastActivatorCLSID, path)
                if not clsid:
                    continue
                aumid = _property(None, PKEY_AppUserModelID, path)
                if aumid:
                    found.setdefault(aumid.lower(), clsid)
    return found


def toast_activator(aumid):
    """CLSID of the app's toast activator COM server, or None."""
    if aumid in _activators:
        return _activators[aumid]
    clsid = None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        clsid = _reg_value(root, rf"Software\Classes\AppUserModelId\{aumid}", "CustomActivator")
        if clsid:
            break
    if not clsid:
        clsid = _property(aumid, PKEY_ToastActivatorCLSID)
    if not clsid:
        clsid = _shortcut_activators().get(aumid.lower())
    if not clsid and "!" in aumid:
        clsid = _manifest_activator(aumid)
    if clsid and clsid.strip("{}").replace("0", "").replace("-", "") == "":
        clsid = None
    _activators[aumid] = clsid
    return clsid


def _manifest_activator(aumid):
    """A packaged desktop app (WhatsApp, ...) declares its toast activator in
    its AppxManifest.xml, not on the Start menu entry. Without it a reply
    could only launch the app, and the typed text would be lost."""
    folder = app_folder(aumid)
    if not folder:
        return None
    try:
        root = ET.parse(os.path.join(folder, "AppxManifest.xml")).getroot()
    except (OSError, ET.ParseError):
        return None
    app_id = aumid.split("!", 1)[1]
    apps = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Application"]
    apps.sort(key=lambda e: e.get("Id") != app_id)  # this entry first, then the others
    for app in apps:
        for e in app.iter():
            if e.tag.rsplit("}", 1)[-1] == "ToastNotificationActivation" and e.get("ToastActivatorCLSID"):
                return "{" + e.get("ToastActivatorCLSID").strip("{}").upper() + "}"
    return None


# ---------------------------------------------------------------------------
# App name and icon

_names = {}


def pretty_aumid(aumid):
    s = aumid.split("!")[0]
    s = re.sub(r"_[a-z0-9]{13}$", "", s)          # package family hash
    s = s.split("\\")[-1]
    s = re.sub(r"\.exe$", "", s, flags=re.I)
    parts = [p for p in re.split(r"[.]", s) if p and not re.fullmatch(r"com|app|desktop|squirrel|electron|microsoft|[0-9A-F]{5,}", p, re.I)]
    name = parts[-1] if parts else s
    return name[:1].upper() + name[1:]


def app_name(aumid, feed=None):
    if aumid in _names:
        return _names[aumid]
    name = None
    if feed:
        name = feed.handler_asset(aumid, "DisplayName")
    if not name:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            name = _reg_value(root, rf"Software\Classes\AppUserModelId\{aumid}", "DisplayName")
            if name:
                break
    if not name or name.startswith("@") or name.startswith("ms-resource:"):
        name = _property(aumid, PKEY_ItemNameDisplay)
    if not name:
        name = pretty_aumid(aumid)
    _names[aumid] = name
    return name


def _png(width, height, rgba):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD),
                ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HANDLE, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HANDLE]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


def _hbitmap_png(hbm):
    bm = BITMAP()
    gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm))
    w, h = bm.bmWidth, bm.bmHeight
    if w <= 0 or h <= 0:
        return None
    bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = (ctypes.c_ubyte * (w * h * 4))()
    hdc = user32.GetDC(None)
    try:
        if not gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bih), 0):
            return None
    finally:
        user32.ReleaseDC(None, hdc)
    px = bytearray(buf)
    has_alpha = any(px[i] for i in range(3, len(px), 4))
    for i in range(0, len(px), 4):
        b, g, r, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if not has_alpha:
            a = 255
        elif 0 < a < 255:  # un-premultiply
            r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
        px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a
    return _png(w, h, bytes(px))


def _shell_png(parsing_name, px, flags):
    """PNG bytes of the shell's image for a path or shell: name, at most px
    square (flag 0x1 lets icons come back bigger), or None."""
    com_init()
    factory = ctypes.c_void_p()
    try:
        shell32.SHCreateItemFromParsingName(
            ctypes.c_wchar_p(parsing_name), None,
            ctypes.byref(guid(IID_IShellItemImageFactory)), ctypes.byref(factory))
    except OSError:
        return None
    hbm = wt.HANDLE()
    try:
        method(factory, 3, [SIZE, ctypes.c_int, ctypes.POINTER(wt.HANDLE)])(
            factory, SIZE(px, px), flags, ctypes.byref(hbm))
        return _hbitmap_png(hbm)
    except OSError:
        return None
    finally:
        if hbm:
            gdi32.DeleteObject(hbm)
        release(factory)


def app_icon(aumid, cache_dir, feed=None):
    """Path to a PNG of the app's icon (cached), or None."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", aumid)[-80:]
    path = os.path.join(cache_dir, f"icon-{safe}-{zlib.crc32(aumid.encode()):08x}.png")
    if os.path.isfile(path):
        return path
    if feed:
        uri = feed.handler_asset(aumid, "IconUri")
        local = local_image(uri, cache_dir) if uri else None
        if local:
            return local
    com_init()
    factory = ctypes.c_void_p()
    try:
        shell32.SHCreateItemFromParsingName(
            ctypes.c_wchar_p(apps_folder(aumid)), None,
            ctypes.byref(guid(IID_IShellItemImageFactory)), ctypes.byref(factory))
    except OSError:
        return None
    hbm = wt.HANDLE()
    try:
        method(factory, 3, [SIZE, ctypes.c_int, ctypes.POINTER(wt.HANDLE)])(
            factory, SIZE(64, 64), 0x1 | 0x4, ctypes.byref(hbm))  # BIGGERSIZEOK | ICONONLY
        data = _hbitmap_png(hbm)
    except OSError:
        return None
    finally:
        if hbm:
            gdi32.DeleteObject(hbm)
        release(factory)
    if not data:
        return None
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---------------------------------------------------------------------------
# Activation: doing what a click on the toast would do


def _allow_foreground():
    """Lets the app we activate bring its window to the front. Windows only
    hands that right to the process with the latest input, so tap Alt first."""
    try:
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.AllowSetForegroundWindow(wt.DWORD(-1 & 0xFFFFFFFF))
    except Exception:
        pass


def _com_activate(clsid, aumid, args, inputs):
    unk = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(guid(clsid)), None, CLSCTX_LOCAL_SERVER,
                           ctypes.byref(guid(IID_INotificationActivationCallback)), ctypes.byref(unk))
    try:
        try:
            ole32.CoAllowSetForegroundWindow(unk, None)
        except OSError:
            pass
        items = list(inputs.items())
        arr = (NOTIFICATION_USER_INPUT_DATA * max(1, len(items)))()
        for i, (k, v) in enumerate(items):
            arr[i].Key, arr[i].Value = k, v
        method(unk, 3, [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.ULONG])(
            unk, aumid, args, ctypes.cast(arr, ctypes.c_void_p) if items else None, len(items))
    finally:
        release(unk)


def _packaged_activate(aumid, args):
    mgr = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(guid(CLSID_ApplicationActivationManager)), None, CLSCTX_ALL,
                           ctypes.byref(guid(IID_IApplicationActivationManager)), ctypes.byref(mgr))
    try:
        pid = wt.DWORD()
        method(mgr, 3, [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.DWORD)])(
            mgr, aumid, args or None, 0, ctypes.byref(pid))
    finally:
        release(mgr)


def activate(aumid, args="", activation="foreground", inputs=None, log=None, launch_only=False):
    """Runs on a worker thread so a slow app never stalls the plugin.
    launch_only: no notification to act on any more, just bring the app up."""
    def work():
        com_init()
        _allow_foreground()
        steps = []
        if activation == "protocol" and args:
            steps.append(("protocol", lambda: os.startfile(args)))
        clsid = None if launch_only else toast_activator(aumid)
        if clsid and activation != "protocol":
            steps.append(("activator " + clsid, lambda: _com_activate(clsid, aumid, args, inputs or {})))
        if "!" in aumid and activation != "protocol":
            steps.append(("packaged", lambda: _packaged_activate(aumid, args)))
        steps.append(("apps folder", lambda: os.startfile(apps_folder(aumid))))
        for name, fn in steps:
            try:
                fn()
                if log:
                    log(f"opened {aumid} via {name}")
                return
            except Exception as e:  # try the next way
                if log:
                    log(f"{name} failed for {aumid}: {e}", "warn")
    threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------------------
# Banners


def known_apps():
    """AppUserModelIDs Windows keeps notification settings for."""
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


def banner_state(aumid):
    """0 when banners are off for the app, 1 when on, None when unset (on)."""
    return _reg_value(winreg.HKEY_CURRENT_USER, rf"{SETTINGS_KEY}\{aumid}", "ShowBanner")


def set_banner(aumid, show):
    """Off: the app's toasts go straight to the notification center (where
    this plugin reads them) without a popup. On: back to Windows' default."""
    path = rf"{SETTINGS_KEY}\{aumid}"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            if show:
                try:
                    winreg.DeleteValue(k, "ShowBanner")
                except OSError:
                    pass
            else:
                winreg.SetValueEx(k, "ShowBanner", 0, winreg.REG_DWORD, 0)
        return True
    except OSError:
        return False


def filetime_to_unix(ft):
    return ft / 10_000_000 - 11_644_473_600 if ft else time.time()


# ---------------------------------------------------------------------------
# Sounds: playing our own, and muting Windows' notification sounds

MEDIA = os.path.expandvars(r"%WINDIR%\Media")
BUILTIN_SOUNDS = [  # (id, name, file in C:\Windows\Media)
    ("notify", "Notify", "Windows Notify System Generic.wav"),
    ("message", "Message", "Windows Notify Messaging.wav"),
    ("email", "Email", "Windows Notify Email.wav"),
    ("calendar", "Calendar", "Windows Notify Calendar.wav"),
    ("nudge", "Nudge", "Windows Message Nudge.wav"),
    ("ding", "Ding", "Windows Ding.wav"),
    ("chimes", "Chimes", "chimes.wav"),
    ("tada", "Tada", "tada.wav"),
]
SOUND_FILE_TYPES = (".wav", ".mp3", ".wma", ".m4a", ".aac", ".flac")

winmm = ctypes.WinDLL("winmm")
winmm.mciSendStringW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.UINT, wt.HANDLE]
winmm.mciSendStringW.restype = wt.DWORD


class SoundPlayer:
    """Plays WAV/MP3/... through Windows' MCI (no extra installs), at a
    volume. Each sound gets its own alias, closed once it has had time to end."""

    def __init__(self):
        self.n = 0
        self.open = []  # (alias, monotonic time opened)

    @staticmethod
    def _mci(cmd):
        buf = ctypes.create_unicode_buffer(256)
        err = winmm.mciSendStringW(cmd, buf, 256, None)
        return err, buf.value

    def play(self, path, volume=70):
        self.cleanup(force=True)
        if not path or not os.path.isfile(path):
            return False
        self.n += 1
        alias = f"fennsnd{self.n}"
        # mpegvideo (DirectShow) plays WAV and MP3 alike and supports a volume.
        err, _ = self._mci(f'open "{path}" type mpegvideo alias {alias}')
        if err:
            return False
        self._mci(f"setaudio {alias} volume to {max(0, min(1000, int(volume * 10)))}")
        err, _ = self._mci(f"play {alias}")
        self.open.append((alias, time.monotonic()))
        return not err

    def cleanup(self, force=False):
        keep = []
        for alias, at in self.open:
            if force or time.monotonic() - at > 30:
                self._mci(f"close {alias}")
            else:
                keep.append((alias, at))
        self.open = keep


SOUND_EVENTS_KEY = r"AppEvents\Schemes\Apps\.Default"
# Toast sounds. Alarms and calls (Notification.Looping.*) keep ringing.
WINDOWS_SOUND_EVENTS = ["Notification.Default", "Notification.IM", "Notification.Mail",
                        "Notification.Reminder", "Notification.SMS", "Notification.Proximity"]


def mute_windows_sounds(mute, saved):
    """Off: sets Windows' notification sound events to "(None)", the same as
    Control Panel > Sound, and returns what they were. On: puts them back from
    `saved`. Applies right away."""
    out = dict(saved or {})
    for event in WINDOWS_SOUND_EVENTS:
        path = rf"{SOUND_EVENTS_KEY}\{event}\.Current"
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                                    winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
                try:
                    current, kind = winreg.QueryValueEx(k, "")
                except OSError:
                    current, kind = None, winreg.REG_SZ
                if mute:
                    if event not in out and current:  # remember the real sound (and its type) once
                        out[event] = [current, kind]
                    winreg.SetValueEx(k, "", 0, kind, "")
                elif event in out:
                    value = out.pop(event)
                    value, kind = (value if isinstance(value, list) else [value, kind])
                    winreg.SetValueEx(k, "", 0, kind, value)
        except OSError:
            pass
    return out


# ---------------------------------------------------------------------------
# Ringtones for calls

RINGTONES = [(f"ring{i:02d}", f"Ring {i}", f"Ring{i:02d}.wav") for i in range(1, 11)] + [
    ("ringin", "Classic ring", "Windows Ringin.wav")]
RING_TYPES = SOUND_FILE_TYPES + (".ogg", ".opus")
_ring_cache = {}  # aumid -> (path, where it came from)


def windows_event_sound(event):
    """The file Windows plays for a sound event (Control Panel > Sound),
    e.g. Notification.Looping.Call -> Windows' Ring01.wav."""
    path = _reg_value(winreg.HKEY_CURRENT_USER, rf"{SOUND_EVENTS_KEY}\{event}\.Current", "")
    path = os.path.expandvars(path or "")
    if path and os.path.isfile(path):
        return path
    m = re.search(r"Looping\.Call(\d*)$", event)
    if m:  # Windows' defaults: Call -> Ring01, Call2 -> Ring02 ...
        guess = os.path.join(MEDIA, f"Ring{int(m.group(1) or 1):02d}.wav")
        if os.path.isfile(guess):
            return guess
    return None


PKEY_PackageInstallPath = pkey("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 15)
PKEY_LinkTarget = pkey("{B9B4B3FC-2B51-4A42-B5D8-324146AFCF25}", 2)


def app_folder(aumid):
    """Where an app is installed: a Store app's package folder, or the
    folder of the program its Start menu entry points to."""
    path = _property(aumid, PKEY_PackageInstallPath)
    if path and os.path.isdir(path):
        return path
    target = _property(aumid, PKEY_LinkTarget)
    if target and os.path.isfile(target):
        return os.path.dirname(target)
    if "!" in aumid:  # a packaged app's other entry (e.g. Phone Link's calls)
        family = aumid.split("!")[0]
        for k in (family + "!App",):
            path = _property(k, PKEY_PackageInstallPath)
            if path and os.path.isdir(path):
                return path
    return None


def _ring_score(name):
    """How much a sound file's name looks like an incoming-call ringtone."""
    n = name.lower()
    if "ring" not in n and "incoming" not in n:
        return 0
    if any(w in n for w in ("ringout", "ringback", "outgoing", "hangup", "hang_up", "end", "dial", "busy")):
        return 0
    return 3 if "ringtone" in n else 2 if "incoming" in n else 1


def _search_folder(folder, limit=40000):
    best, seen = (0, None), 0
    for root, dirs, files in os.walk(folder):
        depth = root[len(folder):].count(os.sep)
        if depth >= 5:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d.lower() not in ("node_modules", "locales", "swiftshader")]
        for f in files:
            seen += 1
            if f.lower().endswith(RING_TYPES):
                s = _ring_score(f)
                if s > best[0]:
                    best = (s, os.path.join(root, f))
        if seen > limit:
            break
    return best


def _search_asar(asar, cache_dir):
    """Electron apps keep their files in resources/app.asar: find a ringtone
    in its index and copy it out."""
    try:
        with open(asar, "rb") as f:
            head = f.read(16)
            size = struct.unpack("<I", head[12:16])[0]
            index = json.loads(f.read(size).decode("utf-8"))
            base = 8 + struct.unpack("<I", head[4:8])[0]
            best = (0, None, None)

            def walk(node, path):
                nonlocal best
                for name, v in (node.get("files") or {}).items():
                    if "files" in v:
                        walk(v, path + "/" + name)
                    elif name.lower().endswith(RING_TYPES) and not v.get("unpacked"):
                        s = _ring_score(name)
                        if s > best[0]:
                            best = (s, name, v)
            walk(index, "")
            if not best[1]:
                return (0, None)
            os.makedirs(cache_dir, exist_ok=True)
            out = os.path.join(cache_dir, "ring-" + re.sub(r"[^\w.-]", "_", best[1]))
            if not os.path.isfile(out):
                f.seek(base + int(best[2]["offset"]))
                data = f.read(int(best[2]["size"]))
                with open(out, "wb") as o:
                    o.write(data)
            return (best[0], out)
    except (OSError, ValueError, KeyError, struct.error):
        return (0, None)


def app_ringtone(aumid, info, cache_dir):
    """The ringtone an app uses for calls, as a file: the sound its call
    notification names, a ringtone in the app's own folder (or inside an
    Electron app's app.asar), or Windows' call sound. -> (path, where)."""
    audio = (info or {}).get("audio", "")
    if audio.startswith("ms-winsoundevent:"):
        path = windows_event_sound(audio.split(":", 1)[1])
        if path:
            return path, "the sound its call notification uses"
    folder = None
    if audio.startswith("ms-appx:///") or audio.startswith("file:///"):
        folder = app_folder(aumid)
        rel = urllib.parse.unquote(audio.split(":///", 1)[1]).replace("/", os.sep)
        path = os.path.join(folder, rel) if audio.startswith("ms-appx") and folder else rel
        if path and os.path.isfile(path):
            return path, "the sound its call notification uses"
    if aumid in _ring_cache:
        return _ring_cache[aumid]
    found = (None, "")
    folder = folder or app_folder(aumid)
    if folder:
        score, path = _search_folder(folder)
        asar = os.path.join(folder, "resources", "app.asar")
        if os.path.isfile(asar):
            s2, p2 = _search_asar(asar, cache_dir)
            if s2 > score:
                score, path = s2, p2
        if path:
            found = (path, "the app's own ringtone")
    if not found[0]:
        path = windows_event_sound("Notification.Looping.Call")
        found = (path, "Windows' call sound (the app has none of its own)")
    _ring_cache[aumid] = found
    return found
