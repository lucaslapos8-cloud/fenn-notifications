# Notifications for Fenn

Windows notifications pop out of Fenn's island instead of the corner of the screen.

When a notification arrives, a small bubble grows out of the pill with the app
icon, the sender and the message. A few seconds later it shrinks back into the
pill. Click it to open the chat or page it came from, the same as clicking the
Windows notification.

- **Hover** to keep it open and see the reply box, the app's own button
  (for example *Mark as Read*) and **Mute for**: 15 minutes, 1 hour or until
  you unmute. Type and press Enter to reply.
- **Right-click** or click **✕** to dismiss.
- **Looks:** Tinted (smoked glass, default) with four tint strengths, Glass,
  or Solid.
- **Sizes:** XS (as small as Fenn's pill, one line), S, M, L, XL.
- **Sender photos** with a small app icon on them, like on iPhone: taken from
  the notification itself (Discord, Telegram, WhatsApp, Chrome/Edge web apps
  and others that include one), or for Beeper from its local contact list and
  chat pictures. Photos that appear later are filled into the history.
- **Unread marker** between the time and the date in Fenn's clock pill: a
  small dot or a count, for 10 seconds after the bubble closes (or off, or
  until read). It hides while you hover the pill or when the pill shows
  something other than the clock. Or use Fenn's own badge instead.
- **History** like a messages list: one row per sender with their photo, the
  latest message, the time (now, 5m, Yesterday, ...) and an unread dot. Click
  the arrow on a row to see that sender's older messages; click any message to
  open the chat. The settings are below the list, in three tabs: Bubble,
  Options and Sounds.
- **Mute an app** from the bubble or with the bell next to it in the Sounds
  tab (15 minutes, 1 hour, until you unmute). Muted apps still go into the
  history, but don't pop out or play a sound.
- **Sounds:** your own notification sound instead of Windows': pick one of
  Windows' built-in sounds or drop an MP3/WAV on the plugin's page, set the
  volume, and give apps their own sound (or none). Optionally mutes Windows'
  own notification sounds (alarms and calls keep ringing).
- Skips full-screen games, videos and presentations unless you turn that on.
- Notifications you dismiss in Windows disappear here too.

## Requirements

- Windows 10 2004 or later, or Windows 11
- Python 3.8+ (Fenn offers to download it if it's missing)
- Windows PowerShell 5.1, which ships with Windows (draws the bubble with WPF)

Nothing else to install: it uses only the Python standard library.

## Install

Use the **Install in Fenn** button on the marketplace listing, or by hand:

```powershell
git clone https://github.com/lucaslapos8-cloud/fenn-notifications.git "$env:APPDATA\Fenn\plugins\notifications"
```

## No double alerts

Windows still shows its own banner in the corner by default. To see
notifications only in the island, open the plugin's page in Fenn and turn on
**Hide Windows banners**. This switches off *Show notification banners* for
each app in Windows Settings, so notifications go straight to the notification
center, where the plugin reads them. Turning the switch off restores the
banners for exactly the apps it changed.

If an app still shows banners, sign out and back in once, or turn on Windows
**Do not disturb**: it hides every banner but still collects the
notifications, so the island still shows them.

Apps that have notifications switched off entirely in Windows Settings won't
reach the island either. Turn them back on (banners can stay off).

## How it works

- **Reading:** polls the Windows notification database
  (`%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db`) read-only,
  a few times a second.
- **Opening the right chat:** sends the notification's own launch data to the
  app the way Windows does. That is the app's toast activator found through its
  Start menu shortcut, a protocol link, or a packaged-app launch. It falls back
  to just opening the app.
- **The bubble:** a separate, focus-free WPF window (`bubble.ps1`) drawn over
  Fenn's pill, so Fenn's own notch never opens or pins. While the bubble is
  out, a low-level mouse hook routes clicks on the part of the bubble above
  Fenn's pill to the bubble only, because Fenn tracks the cursor itself.
- **Glass looks:** use a blurred snapshot of the screen behind the bubble,
  taken when it opens. Nothing is saved.

## Privacy and network

- Message text stays in memory only and is never written to disk. The data
  folder (`FENN_DATA_DIR`) holds settings and cached app icons.
- The plugin makes only two kinds of network calls, both downloads that are
  cached in its data folder:
  - if a notification's picture is a web address (some apps send sender
    avatars this way), that one image;
  - **color emoji:** the bubble can't draw color emoji from the font, so each
    emoji in a message is fetched once as a picture from
    `cdn.jsdelivr.net/gh/jdecked/twemoji` (only the emoji's code is sent, never
    the message). Switch off "Color emoji" to skip it.
- **Sender photos for Beeper:** Beeper's notifications carry no picture, so
  the plugin looks the sender's name up in Beeper's local database
  (`%APPDATA%\BeeperTexts\index.db`, read-only, contacts table only) and copies
  their already-downloaded avatar into its cache. Nothing leaves your PC.
  Switch off **Sender photos** to skip this.
- With **Mute Windows' own notification sounds** on, the plugin sets the
  Notification sound events under `HKCU\AppEvents\Schemes\Apps\.Default` to
  "(None)" (like Control Panel > Sound) and restores the previous sounds when
  you switch it off. Sound files you drop are copied into the data folder.
- With **Hide Windows banners** on, the plugin writes `ShowBanner` values under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings`, and
  removes them again when you switch it off.

## Known limits

- Some apps (Beeper, for example) don't put the chat into their notification.
  Clicking then opens the app, but may not jump to the exact chat.
- Glass looks show a snapshot from when the bubble opened: video playing
  behind it doesn't move in the blur.
- The bubble and the unread marker follow Fenn's own settings (position,
  monitor, margin, pill or notch shape, auto-hide) by reading Fenn's
  `config.toml` once a second. On mixed-DPI multi-monitor setups the position
  on a secondary monitor can be a few pixels off.

## License

MIT, see [LICENSE](LICENSE). `fenn.py` is Fenn's plugin helper (MIT, © The
Nook contributors). Color emoji are [Twemoji](https://github.com/jdecked/twemoji)
graphics (CC-BY 4.0, © Twitter, Inc. and other contributors), downloaded at runtime.

## Changelog

**1.3.1**
- Mute an app for 15 minutes, 1 hour or until you unmute: from the bubble's
  new "Mute for" button (it replaces the app's second button) or the bell
  next to each app in the Sounds tab, which also shows the time left.
  Muted apps still go into the history but stay silent.

**1.3.0**
- Notification sounds: Windows' built-in sounds or your own MP3/WAV (drop it
  on the page), a volume, and a sound and volume for each app (or none).
  Optionally mutes Windows' own notification sounds; alarms and calls keep
  ringing.
- Color emoji in the bubble (Twemoji pictures, downloaded once each).
- Settings in three tabs: Bubble, Options and Sounds.
- Apps stay in the list after the history is cleared, so their sound and
  volume can be set anytime.

**1.2.2**
- Fixed: older messages in the history didn't open their chat in apps that
  remove their notifications once a chat is read (like Beeper).
- Sender photos from packaged apps (WhatsApp, Phone Link, ...) that store
  them in their own data folder.
- Beeper: group chats show the group's picture, and photos of new contacts
  are filled in once Beeper has downloaded them.

**1.2.1**
- Follows Fenn's notch settings live: left/center/right, primary/secondary/
  follow-cursor monitor, margin, pill or notch shape.
- Notch shape: the bubble hangs from the top edge with curved corners that
  flare into it, like Fenn's open notch.
- With Fenn's auto-hide on, the bubble flies down from the top edge while
  growing, closes the same way in reverse, and the unread marker stays hidden.
- The unread marker finds the gap between the time and the date on screen.

**1.2.0**
- Fixed: notifications stopped popping up after Windows cleared old ones.
- Sender photos with a small app icon (Beeper photos from its local contacts).
- Unread marker (dot or count) between the time and the date in the pill,
  clearing after 10 seconds (or off, or until read).
- History redesigned as a messages list with expandable conversations.
- Tinted look with four strengths; stronger, smoother blur.

**1.1.0**
- First release.
