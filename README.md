# Notifications for Fenn

Windows notifications pop out of Fenn's island instead of the corner of the
screen. Click one to open the chat it came from, reply right in the bubble,
take calls, and keep a messages-style history in the island.

> **New in 1.4.0:** every notification opens its own chat, however old and
> however often clicked · apps come up instantly · Discord chats open through
> Windows' notification center or Discord's quick switcher · an **Apps** tab
> shows how each chat app opens · **Report a bug** and **Help** buttons.
> Coming from 1.3.1? Calls, ringtones and WhatsApp replies arrived in 1.3.2.
> See the [changelog](#changelog).

## Contents

- [Quick start](#quick-start)
- [What it does](#what-it-does)
- [Opening the right chat](#opening-the-right-chat)
- [App notes (Discord, Beeper, WhatsApp, ...)](#app-notes)
- [Settings](#settings)
- [Known limits](#known-limits)
- [Report a bug or ask for an app](#report-a-bug-or-ask-for-an-app)
- [Privacy and network](#privacy-and-network)
- [How it works (for developers)](#how-it-works-for-developers)
- [Requirements and install](#requirements-and-install)
- [Changelog](#changelog)

## Quick start

1. Install it from Fenn's plugin list (**Install in Fenn**), or by hand (see
   [Requirements and install](#requirements-and-install)).
2. Open the plugin's page in Fenn (click its tile). The settings are below
   the notification list, in tabs: **Bubble · Options · Sounds · Calls · Apps**.
3. **No double pop-ups:** in **Options**, turn on **Hide Windows banners**.
   Windows then stops showing its own banner in the corner; notifications
   still reach the notification center, where the plugin reads them.
4. **Using Discord?** Read [Discord](#discord) below: it behaves differently
   from other apps, and one option (**quick switcher**) is off by default.
5. Something not working? **Options > Report a bug**.

## What it does

- **The bubble:** a small bubble grows out of Fenn's pill with the sender's
  photo (the app's icon on it), the name and the message, then shrinks back
  after a few seconds. **Click** it to open the chat. **Hover** to keep it
  open and see the **reply box**, the app's own button (e.g. *Mark as Read*,
  or WhatsApp's *Mute chat for 8 hours*) and **Mute for**. **Right-click** or
  **✕** to dismiss.
- **Replies** go straight to the app, like a reply typed into Windows' own
  notification (WhatsApp included); the app sends them without opening.
- **Calls** from any app (WhatsApp, Phone Link, Beeper, ...) show as calls: a
  green phone with the app's icon, the caller's name, and **Accept**,
  **Decline** and **Mute** in the bubble and in the list. Mute silences only
  that call. Optional **ringtones**: each app's own, Windows' or your own file.
- **History** like a messages list: one row per sender with their photo, the
  latest message, the time and an unread dot. The arrow on a row shows that
  sender's older messages. **Every message stays clickable**, however old.
- **Sounds:** your own notification sound instead of Windows', a sound and
  volume per app, and **mute an app** for 15 minutes, 1 hour or until you
  unmute. Muted apps still go into the history.
- **Unread marker** in Fenn's clock pill (a dot or a count), or Fenn's own badge.
- **Looks:** Tinted (four strengths), Glass or Solid, in five sizes (XS to XL).
  The bubble follows Fenn's notch settings (position, monitor, shape, auto-hide).
- Stays quiet over full-screen games, videos and presentations (optional).
- Notifications you dismiss in Windows disappear here too.

## Opening the right chat

A click on a message (in the bubble or the history) tries, in this order:

| | Step | Used for |
|---|---|---|
| 1 | **The app's chat link**, worked out from the app's own data | Beeper |
| 2 | **Windows' notification center**: the plugin opens it for a moment and clicks the notification, like you would | Apps whose notifications don't say which chat (Discord), and already-clicked ones of apps that forget them |
| 3 | **The notification's own click data**, sent the way Windows does | Most apps: WhatsApp, Telegram, Teams, Signal, Slack, ... |
| 4 | **Discord's quick switcher** (Ctrl+K, name or #channel, Enter), *option, off by default* | Discord messages Windows no longer has |
| 5 | **The app itself**, or a list the chat is in (Discord: Direct Messages) | Anything the steps above can't reach |

Whatever happens, the app's window comes up **at once**: out of the tray, the
taskbar or from behind other windows, without starting a second copy. An app
that isn't running is started and brought up as soon as its window appears.

The **Apps** tab shows how this works for each chat app you use:
**green** = the exact chat every time, **yellow** = the exact chat while
Windows still has the notification, **orange** = the app only.

## App notes

| App | Click opens | Notes |
|---|---|---|
| **WhatsApp** | the exact chat | Replies from the bubble are sent directly. Its ringtone is used for calls. |
| **Telegram, Teams, Signal, Slack, most others** | the exact chat | Their notifications carry the chat. |
| **Beeper** | the exact chat, every time | Found in Beeper's local database (read-only), opened with Beeper's own chat link. |
| **Phone Link** | the conversation / call | Calls show the caller and Accept/Decline. |
| **Discord** | see below | Discord needs extra care. |

### Discord

Discord's notifications don't say which chat they're from, so only Windows'
own click can open the exact chat. The plugin does that click for you in the
notification center (**Options > Open the exact chat through Windows'
notification center**, on by default). But:

- **DMs:** Discord deletes a DM's notification from Windows after about
  **2.5 seconds**. Click the bubble quickly and the exact DM opens; later
  clicks open the **Direct Messages list** (newest DM on top).
- **Server messages** stay in Windows until clicked, so the first click opens
  the exact channel; later clicks open Discord.
- **Quick switcher (optional, off by default):** turn on **Options > Open
  Discord chats through Discord's quick switcher** and later clicks open the
  exact chat too: the plugin presses Ctrl+K in Discord, types the sender's
  name (a DM) or `#channel` (a server message) and presses Enter, but only
  after checking that Discord's switcher has the text and the highlighted
  result is that chat. Any doubt and it stops (Esc) instead of guessing.
  - Channel names used in several of your servers (like `#general`) can't be
    told apart: it stops and opens Discord.
  - A bot or app posting inside someone else's DM (e.g. Wordle via /share)
    is titled with its own name, so its own DM opens.
  - If Discord moves focus at the wrong moment, the typed name can end up as
    **unsent** text in the open chat's message box. Enter is never pressed
    then; just delete the text.

## Settings

**Bubble:** pop out when one arrives · collapse after 4/6/10/20 s · look
(Solid, Glass, Tinted) and tint strength · size XS-XL · unread marker (dot,
count or Fenn's badge) and how long it shows.

**Options:**
- **Sender photos** (Beeper: from its local contact list)
- **Color emoji** in the bubble (Twemoji pictures, downloaded once each)
- **Also pop out over full-screen apps and games**
- **Open the exact chat through Windows' notification center** (on)
- **Open Discord chats through Discord's quick switcher** (off)
- **Hide Windows banners**: only the island shows notifications. Switching it
  off restores the banners for exactly the apps it changed. If an app keeps
  showing banners, sign out and in once, or turn on Do not disturb.
- **Report a bug** and **Help** buttons

**Sounds:** play a sound with each notification · the sound (Windows' or your
own MP3/WAV, dropped on the page) · volume · mute Windows' own notification
sounds (alarms and calls keep ringing) · per app: mute (15 min, 1 hour, until
unmuted), its own sound and volume.

**Calls:** ring for incoming calls (off by default) · ringtone (the app's own,
Windows' or your own MP3/WAV/M4A) · volume · ring for 20 s to 1 min · per app:
calls on/off (off = no bubble, no ringtone, still in the history), its own
ringtone and volume. For a quieter app, use its volume slider.

**Apps:** each chat app that has sent a message and how a click opens it, with
**Request support** for apps that can't open the exact chat every time.
**Show all apps** lists the non-chat apps too.

## Known limits

- **Discord DMs** open exactly only within about 2.5 seconds, or with the
  quick-switcher option; see [Discord](#discord).
- **Apps whose notifications don't name the chat** (like Discord) open the
  exact chat only while Windows still has the notification. The plugin
  then shows the notification center for about a second, moves the mouse
  pointer there and back, and presses Win+N. A Windows update that changes
  the notification center can make this miss; clicks then open the app.
- **Apps that forget a notification after one click** (many Electron apps)
  open the exact chat again only while another notification of that chat is
  left, unless they have a helper (Beeper does).
- If Beeper changes its database in an update, Beeper clicks fall back to
  the steps above.
- Apps with notifications switched off in Windows Settings don't reach the
  island. Turn them back on (banners can stay off).
- Glass looks show a snapshot from when the bubble opened (video behind it
  doesn't move in the blur).
- On mixed-DPI multi-monitor setups the bubble can sit a few pixels off on a
  secondary monitor.

## Report a bug or ask for an app

- **Bug:** **Options > Report a bug** opens a new GitHub issue with the plugin
  and Windows version filled in. Or open one at
  [github.com/lucaslapos8-cloud/fenn-notifications/issues](https://github.com/lucaslapos8-cloud/fenn-notifications/issues).
  Fenn's log (`%APPDATA%\Fenn\fenn.log`, lines with `[plugin:notifications]`)
  helps; check it for names or message text before pasting.
- **An app that doesn't open the exact chat:** **Apps > Request support**
  opens an issue with that app's name and id filled in. Nothing is sent until
  you submit it yourself. If the app has a "Copy link" option for chats,
  mention what such a link looks like (with names and numbers replaced).

## Privacy and network

- Message text stays in memory only and is never written to disk. The data
  folder (`FENN_DATA_DIR`) holds settings, cached app icons and an empty
  `reloaded` marker (the plugin restarts itself when its files change, at
  most every 25 seconds).
- Only two kinds of downloads, both cached in the data folder: a
  notification's picture when an app sends it as a web address, and color
  emoji from `cdn.jsdelivr.net/gh/jdecked/twemoji` (only the emoji's code is
  sent, never the message; switch off **Color emoji** to skip it).
- **Beeper**, read-only from `%APPDATA%\BeeperTexts\index.db`: sender photos
  (contacts; switch off **Sender photos** to skip) and, on a click, the
  messages from around the notification's arrival time, the chat list and the
  chat members' names, to find the chat. Nothing is copied or sent; Beeper's
  sign-in data is never read.
- **App ringtones** are looked up in the app's install folder (read-only); one
  inside an Electron app's `app.asar` is copied into the cache.
- **Keyboard and mouse:** only on your click, and only for the
  notification-center click (Win+N, a click on the notification, the pointer
  put back) and Discord's quick switcher (Ctrl+K, the name, Enter, Esc).
- **Hide Windows banners** writes `ShowBanner` values under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings`;
  **Mute Windows' own notification sounds** sets the sound events under
  `HKCU\AppEvents\Schemes\Apps\.Default` to "(None)". Both are undone when
  switched off.
- **Report a bug** and **Request support** only open a page in your browser;
  they send nothing themselves.

## How it works (for developers)

- **Reading:** polls Windows' notification database
  (`%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db`) read-only,
  a few times a second. Toasts that fill in their text from data (Phone Link's
  calls) get their values from its NotificationData table.
- **The bubble** is a separate, focus-free WPF window (`bubble.ps1`) drawn
  over Fenn's pill, so Fenn's notch never opens or pins. While it is out, a
  low-level mouse hook routes clicks on it to the bubble only. It also does the
  notification-center click and the quick switcher (UI Automation, `ncclick`
  and `qswitch` commands).
- **Clicks** go to the app's toast activator (found through its package
  manifest or Start menu shortcut), a protocol link, or a packaged-app launch,
  with the notification's arguments and any reply text. The app's main window
  is found through its install folder and brought to the front directly.
- **Calls** are recognised by the toast's call scenario or ringtone, call
  buttons, a call tag, or words like "Incoming call". Ringtones play in the
  bubble window (WPF MediaPlayer).
- **Helpers for more apps** live in `deeplinks.py`:
  - `HELPERS`: exact chat links, tried first (Beeper);
  - `FALLBACKS`: a link that gets close, used when nothing exact works
    (Discord's Direct Messages list);
  - `SWITCHERS`: what to type into an app's quick switcher (Discord).

  Each takes the notification (app id, title, text, arrival time), only
  reads, and returns None when unsure. List it under the app's
  AppUserModelID.

## Requirements and install

- Windows 10 2004 or later, or Windows 11
- Python 3.8+ (Fenn offers to download it if it's missing), standard library only
- Windows PowerShell 5.1, which ships with Windows

Use **Install in Fenn** on the marketplace listing, or by hand:

```powershell
git clone https://github.com/lucaslapos8-cloud/fenn-notifications.git "$env:APPDATA\Fenn\plugins\notifications"
```

To update by hand, replace the files in that folder (or `git pull`); the
plugin restarts itself.

## License

MIT, see [LICENSE](LICENSE). `fenn.py` is Fenn's plugin helper (MIT, © The
Nook contributors). Color emoji are [Twemoji](https://github.com/jdecked/twemoji)
graphics (CC-BY 4.0, © Twitter, Inc. and other contributors), downloaded at runtime.

## Changelog

**1.4.0**
- **Every notification opens its own chat**, however old and however often
  clicked, from the bubble and the history:
  - apps come up **instantly** (out of the tray, the taskbar or from behind
    other windows) instead of after seconds, and are never started twice;
  - an app that isn't running is started and brought to the front;
  - Beeper chats open through Beeper's own chat link, every time;
  - apps that forget a notification after one click get one of the same chat
    they still remember;
  - apps whose notifications don't say which chat (Discord) get Windows' own
    click through the notification center (Options, on by default).
- **Discord:** exact DMs and channels through the notification center; DMs
  clicked later open the Direct Messages list; new option to open DMs and
  channels through Discord's quick switcher (off by default), with checks so
  it never opens or types into the wrong place.
- **New Apps tab:** how a click opens each chat app (green/yellow/orange),
  with **Request support** for apps that can't open the exact chat. Lists chat
  apps after their first message; **Show all apps** for the rest.
- **Report a bug** and **Help** buttons in Options.
- Fixed: clicking an older message was slow, did nothing, or opened the chat
  that was open last.
- Includes 1.3.3 (published on GitHub only).

**1.3.3**
- Fixed: clicking an older notification was slow or did nothing; apps now
  come up at once. Repeat clicks open the right chat (Beeper's chat link;
  other apps get a notification of the same chat they still remember).

**1.3.2**
- Calls from any app show as calls: a green phone, the app's icon and the
  caller's name, with Accept, Decline and Mute in the bubble and the list.
- Ringtones for calls: each app's own, Windows' or your own file, volume,
  ring length, per-app ringtones and volumes; calls on/off per app.
- Fixed: Phone Link calls showed `{contactName}` and `{incomingCallLabel}`.
- Fixed: replying to WhatsApp (and other Store apps) only opened the app and
  dropped the text (issue #2).
- Apps that keep their extra actions in the notification's right-click menu
  (WhatsApp: "Mute chat for 8 hours") get the first one as a bubble button;
  items that would turn notifications off are never offered.
- The reply box gets its own line in the bubble.
- The plugin restarts itself after an update (e.g. `git pull`).

**1.3.1**
- Mute an app for 15 minutes, 1 hour or until you unmute, from the bubble or
  the Sounds tab.

**1.3.0**
- Notification sounds: Windows' or your own, volume, and per-app sounds;
  optionally mutes Windows' own notification sounds.
- Color emoji in the bubble. Settings in tabs.

**1.2.2**
- Fixed: older messages didn't open their chat in apps that remove their
  notifications once a chat is read (like Beeper).
- Sender photos from packaged apps (WhatsApp, Phone Link, ...); Beeper group
  pictures and new contacts' photos.

**1.2.1**
- Follows Fenn's notch settings live (position, monitor, margin, shape,
  auto-hide); notch shape for the bubble; the unread marker finds the gap
  between the time and the date.

**1.2.0**
- Fixed: notifications stopped popping up after Windows cleared old ones.
- Sender photos, the unread marker, the messages-style history, the Tinted look.

**1.1.0**
- First release.
