# Notifications for Fenn

Windows notifications pop out of Fenn's island instead of the corner of the screen.

When a notification arrives, a small bubble grows out of the pill with the app
icon, the sender and the message. A few seconds later it shrinks back into the
pill. Click it to open the chat or page it came from, the same as clicking the
Windows notification.

- **Hover** to keep it open and see the reply box and the app's own buttons
  (for example *Mark as Read*). Type and press Enter to reply.
- **Right-click** or click **✕** to dismiss.
- **Looks:** Tinted (smoked glass, default) with four tint strengths, Glass,
  or Solid.
- **Sizes:** XS (as small as Fenn's pill, one line), S, M, L, XL.
- **History:** the plugin's page in Fenn lists recent notifications, with
  per-app mute switches and the settings.
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
- The plugin makes no network calls of its own, with one exception: if a
  notification's picture is a web address (some apps send sender avatars this
  way), the plugin downloads that one image so it can show it, and caches it
  in its data folder.
- With **Hide Windows banners** on, the plugin writes `ShowBanner` values under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings`, and
  removes them again when you switch it off.

## Known limits

- Some apps (Beeper, for example) don't put the chat into their notification.
  Clicking then opens the app, but may not jump to the exact chat.
- Glass looks show a snapshot from when the bubble opened: video playing
  behind it doesn't move in the blur.
- The bubble's position assumes Fenn's pill is centered at the top of the
  primary monitor (Fenn's default).

## License

MIT, see [LICENSE](LICENSE). `fenn.py` is Fenn's plugin helper (MIT, © The
Nook contributors).
