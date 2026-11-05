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
import re
import sqlite3
import struct
import threading
import time
import urllib.parse
import urllib.request
import winreg
import xml.etree.ElementTree as ET
import zlib

DB_PATH = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")
SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"

# ---------------------------------------------------------------------------
# Reading notifications


class NotificationFeed:
    """Polls wpndatabase.db for toasts newer than the last one seen."""

    def __init__(self):
        self.conn = None
        self.last_order = None

    def _connect(self):
        if self.conn is None:
            self.conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        return self.conn

    def poll(self):
        """New toasts as dicts: order, id, aumid, tag, group, arrival, payload."""
        try:
            c = self._connect()
            if self.last_order is None:
                row = c.execute('select max("Order") from Notification').fetchone()
                self.last_order = row[0] or 0
                return []
            rows = c.execute(
                'select n."Order", n.Id, h.PrimaryId, n.Tag, n."Group", n.ArrivalTime, n.Payload '
                'from Notification n join NotificationHandler h on h.RecordId = n.HandlerId '
                'where n."Order" > ? and n.Type = \'toast\' order by n."Order"',
                (self.last_order,),
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
        for order, nid, aumid, tag, group, arrival, payload in rows:
            self.last_order = max(self.last_order, order)
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", "replace")
            out.append({"order": order, "id": nid, "aumid": aumid, "tag": tag or "",
                        "group": group or "", "arrival": arrival, "payload": payload or ""})
        return out

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
    actions, inputs, silent). Unknown shapes degrade to plain text."""
    info = {"title": "", "body": "", "attribution": "", "image": None, "hero": None,
            "launch": "", "activation": "foreground", "actions": [], "inputs": [],
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
                if place == "appLogoOverride" and not info["image"]:
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
    actions = root.find("actions")
    if actions is not None:
        for inp in actions.findall("input"):
            info["inputs"].append({"id": inp.get("id", ""), "type": inp.get("type", "text"),
                                   "hint": inp.get("placeHolderContent", "")})
        for act in actions.findall("action"):
            if act.get("placement") == "contextMenu":
                continue
            info["actions"].append({
                "content": act.get("content", ""),
                "arguments": act.get("arguments", ""),
                "activation": (act.get("activationType") or "foreground").lower(),
                "input": act.get("hint-inputId", ""),
            })
    return info


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


def local_image(src, cache_dir):
    """A local file path for a toast image src, or None."""
    if not src:
        return None
    try:
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
    if clsid and clsid.strip("{}").replace("0", "").replace("-", "") == "":
        clsid = None
    _activators[aumid] = clsid
    return clsid


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


def activate(aumid, args="", activation="foreground", inputs=None, log=None):
    """Runs on a worker thread so a slow app never stalls the plugin."""
    def work():
        com_init()
        _allow_foreground()
        steps = []
        if activation == "protocol" and args:
            steps.append(("protocol", lambda: os.startfile(args)))
        clsid = toast_activator(aumid)
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
