# The notification bubble: a small borderless WPF window that grows out of
# Fenn's pill, shows the message and shrinks back into the pill. It never
# takes focus (until you click the reply box) and is click-through outside the
# bubble, so Fenn underneath keeps working. Keep this file ASCII: Windows
# PowerShell reads scripts without a BOM as ANSI.
#
# main.py drives it over stdin, one command per line:
#   theme|dark|#RRGGBB
#   config|<solid|glass|tinted>|<size 0..4>|<tint 0..3>
#   show|id|b64 app|b64 title|b64 body|b64 image|more|seconds|b64 when|b64 reply hint|b64 labels (0x1F separated)|b64 app icon badge
#   marker|<none|dot|count>|<unread>
#   layout|<left|center|right>|<primary|secondary|cursor>|<margin>|<pill|notch>|<autohide 0/1>
#   hide
# and it answers on stdout: ready, click|id, dismiss|id, hidden|id,
# reply|id|b64 text, action|id|index

$ErrorActionPreference = 'Stop'
Add-Type -ReferencedAssemblies PresentationFramework, PresentationCore, WindowsBase, System.Xaml, System.Drawing -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace FennNotify {
public class Bubble : Window {
    // Device-independent pixels. The pill matches Fenn's collapsed notch.
    const double PillW = FennLayout.PillW, PillH = FennLayout.PillH, Wide = 400, CompactW = 340, Pad = 34;
    // Size 0 is the compact, pill-high bubble; 1..4 scale the full bubble.
    static readonly double[] Scales = { 0.85, 0.85, 1.0, 1.15, 1.3 };
    const int GrowMs = 520, ShrinkMs = 380, HoverMs = 260;

    readonly Border card, closeBtn, sendBtn, replyBox;
    readonly Grid inner, content, mainArea, compact, actionsRow, tintLayers;
    readonly Canvas backCanvas;
    readonly Image backdrop;
    readonly Border tintBase, tintColor, sheen;
    readonly Ellipse avatar, cAvatar, appBadge, cAppBadge;
    readonly Border appBadgeRing, cAppBadgeRing;
    readonly TextBlock initial, cInitial, appText, whenText, moreText, titleText, bodyText, cText, replyHint;
    readonly TextBox reply;
    readonly StackPanel buttons;
    readonly System.Windows.Shapes.Path closeGlyph;
    readonly DispatcherTimer tick;

    public Marker marker;
    public bool IsOut { get { return IsVisible && (expanded || collapsing); } }
    string current = "", style = "tinted";
    int tint = 0; // Tinted strength: 0 full (almost black) .. 3 light (more see-through)
    static readonly byte[] TintAlpha = { 0xC4, 0xA6, 0x86, 0x66 };
    int size = 2;
    bool expanded, hovering, collapsing, hoverOpen, typing, hasExtras;
    DateTime collapseAt;
    Color accent = Color.FromRgb(0x7C, 0x5C, 0xFF);
    bool dark = true;
    IntPtr hwnd;

    [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
    [DllImport("user32.dll")] static extern int SetWindowLong(IntPtr h, int i, int v);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr h);

    // Fenn tracks the cursor itself (not the window under it), so a click on
    // the bubble right over its pill would also open Fenn's notch. While the
    // bubble is out, a low-level mouse hook takes those clicks: Fenn never
    // sees them and they are handed straight to the bubble instead.
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct MSLLHOOKSTRUCT { public POINT pt; public uint mouseData, flags, time; public IntPtr extra; }
    delegate IntPtr MouseProc(int code, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] static extern IntPtr SetWindowsHookEx(int id, MouseProc proc, IntPtr mod, uint thread);
    [DllImport("user32.dll")] static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("user32.dll")] static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, int msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] static extern bool ScreenToClient(IntPtr h, ref POINT p);
    [DllImport("kernel32.dll")] static extern IntPtr GetModuleHandle(string name);
    MouseProc mouseProc;
    IntPtr mouseHook;
    int swallowedButton;      // the button whose press we took, so its release goes the same way
    const double FennHalfWidth = 125, FennDepth = 62; // Fenn's pill plus its hover growth, in DIPs

    void HookMouse(bool on) {
        if (on && mouseHook == IntPtr.Zero) {
            if (mouseProc == null) mouseProc = OnMouse;
            mouseHook = SetWindowsHookEx(14, mouseProc, GetModuleHandle(null), 0); // WH_MOUSE_LL
        } else if (!on && mouseHook != IntPtr.Zero) {
            UnhookWindowsHookEx(mouseHook);
            mouseHook = IntPtr.Zero;
            swallowedButton = 0;
        }
    }

    IntPtr OnMouse(int code, IntPtr wParam, IntPtr lParam) {
        if (code >= 0 && hwnd != IntPtr.Zero) {
            int msg = wParam.ToInt32();
            bool down = msg == 0x201 || msg == 0x204 || msg == 0x207;
            bool up = msg == 0x202 || msg == 0x205 || msg == 0x208;
            if (down || up) {
                var info = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
                int button = msg == 0x201 || msg == 0x202 ? 1 : msg == 0x204 || msg == 0x205 ? 2 : 3;
                bool take = up ? swallowedButton == button : (expanded && !collapsing && OverFennPill(info.pt));
                if (take) {
                    swallowedButton = down ? button : 0;
                    var c = info.pt;
                    ScreenToClient(hwnd, ref c);
                    int keys = down ? (button == 1 ? 0x1 : button == 2 ? 0x2 : 0x10) : 0;
                    PostMessage(hwnd, msg, new IntPtr(keys), new IntPtr((c.Y << 16) | (c.X & 0xFFFF)));
                    return new IntPtr(1);
                }
            }
        }
        return CallNextHookEx(mouseHook, code, wParam, lParam);
    }

    bool OverFennPill(POINT pt) {
        try {
            var p = card.PointFromScreen(new Point(pt.X, pt.Y));
            if (p.X < 0 || p.Y < 0 || p.X > card.ActualWidth || p.Y > card.ActualHeight) return false;
            // Fenn's pill (plus the room it grows into on hover), wherever Fenn put it.
            double sc = FennLayout.Scale(), x = pt.X / sc, y = pt.Y / sc;
            return x >= pill.Left - 30 && x <= pill.Right + 30 && y <= pill.Bottom + 28;
        } catch { return false; }
    }

    Rect pill = FennLayout.Pill();
    readonly TranslateTransform slide = new TranslateTransform();

    /// Puts the window where Fenn's pill is: the card grows out of the pill,
    /// away from the screen edge when the notch sits in a corner.
    void Place() {
        pill = FennLayout.Pill();
        string pl = FennLayout.Placement;
        card.HorizontalAlignment = pl == "left" ? HorizontalAlignment.Left : pl == "right" ? HorizontalAlignment.Right : HorizontalAlignment.Center;
        Left = pl == "left" ? pill.Left - Pad : pl == "right" ? pill.Right + Pad - Width : pill.Left + pill.Width / 2 - Width / 2;
        Top = pill.Top;
    }

    double CardLeft() {
        string pl = FennLayout.Placement;
        return pl == "left" ? Pad : pl == "right" ? Width - Pad - card.ActualWidth : (Width - card.ActualWidth) / 2;
    }

    /// A rectangle with its own radius for the top and the bottom corners
    /// (a notch hangs from the screen edge: square on top).
    static Geometry RoundRect(double w, double h, double rt, double rb) {
        rt = Math.Max(0, Math.Min(rt, Math.Min(w, h) / 2));
        rb = Math.Max(0, Math.Min(rb, Math.Min(w, h) / 2));
        var g = new StreamGeometry();
        using (var c = g.Open()) {
            c.BeginFigure(new Point(rt, 0), true, true);
            c.LineTo(new Point(w - rt, 0), true, false);
            if (rt > 0) c.ArcTo(new Point(w, rt), new Size(rt, rt), 0, false, SweepDirection.Clockwise, true, false);
            c.LineTo(new Point(w, h - rb), true, false);
            if (rb > 0) c.ArcTo(new Point(w - rb, h), new Size(rb, rb), 0, false, SweepDirection.Clockwise, true, false);
            c.LineTo(new Point(rb, h), true, false);
            if (rb > 0) c.ArcTo(new Point(0, h - rb), new Size(rb, rb), 0, false, SweepDirection.Clockwise, true, false);
            c.LineTo(new Point(0, rt), true, false);
            if (rt > 0) c.ArcTo(new Point(rt, 0), new Size(rt, rt), 0, false, SweepDirection.Clockwise, true, false);
        }
        g.Freeze();
        return g;
    }

    public Bubble() {
        WindowStyle = WindowStyle.None;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        Topmost = true;
        ShowInTaskbar = false;
        ShowActivated = false;
        ResizeMode = ResizeMode.NoResize;
        Width = Wide * Scales[4] + Pad * 2;
        Height = 160 * Scales[4] + Pad;
        Left = SystemParameters.PrimaryScreenWidth / 2 - Width / 2;
        Top = 0;
        TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display);

        var root = new Grid();
        card = new Border {
            Width = PillW, Height = PillH,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Top,
            BorderThickness = new Thickness(1),
            Background = Brushes.Transparent,
            SnapsToDevicePixels = true,
            Effect = new DropShadowEffect { BlurRadius = 26, ShadowDepth = 5, Direction = 270, Opacity = 0.45, Color = Colors.Black },
            Margin = new Thickness(Pad, 0, Pad, 0),
            RenderTransform = slide,
        };
        root.Children.Add(card);
        Content = root;

        // Layers inside the card, bottom to top: blurred backdrop, tint, sheen, content.
        inner = new Grid { ClipToBounds = true };
        card.Child = inner;
        backCanvas = new Canvas();
        backdrop = new Image { Stretch = Stretch.Fill, Effect = new BlurEffect { Radius = 10, KernelType = KernelType.Gaussian } };
        RenderOptions.SetBitmapScalingMode(backdrop, BitmapScalingMode.HighQuality);
        backCanvas.Children.Add(backdrop);
        inner.Children.Add(backCanvas);
        tintLayers = new Grid();
        tintBase = new Border();
        tintColor = new Border();
        tintLayers.Children.Add(tintBase);
        tintLayers.Children.Add(tintColor);
        inner.Children.Add(tintLayers);
        sheen = new Border();
        inner.Children.Add(sheen);

        card.SizeChanged += (s, e) => Reshape();

        // ---- full layout
        content = new Grid { Width = Wide - 2, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Opacity = 0 };
        content.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        content.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        inner.Children.Add(content);

        mainArea = new Grid { Background = Brushes.Transparent, Cursor = Cursors.Hand };
        mainArea.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        mainArea.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        content.Children.Add(mainArea);

        var face = new Grid { Width = 46, Height = 46, Margin = new Thickness(14, 14, 12, 14), VerticalAlignment = VerticalAlignment.Top };
        avatar = new Ellipse();
        initial = new TextBlock { FontSize = 19, FontWeight = FontWeights.SemiBold, Foreground = Brushes.White,
                                  HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        face.Children.Add(avatar);
        face.Children.Add(initial);
        // Small app icon on the sender's photo, like iOS.
        appBadge = new Ellipse { Width = 18, Height = 18 };
        appBadgeRing = new Border { Width = 22, Height = 22, CornerRadius = new CornerRadius(11), Child = appBadge,
                                    HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom,
                                    Margin = new Thickness(0, 0, -4, -4), Visibility = Visibility.Collapsed };
        face.Children.Add(appBadgeRing);
        mainArea.Children.Add(face);

        var text = new StackPanel { Margin = new Thickness(0, 12, 16, 14) };
        Grid.SetColumn(text, 1);
        mainArea.Children.Add(text);
        var head = new DockPanel { LastChildFill = true };
        closeGlyph = new System.Windows.Shapes.Path {
            Data = Geometry.Parse("M0,0 L7,7 M7,0 L0,7"), StrokeThickness = 1.4,
            StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round,
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        closeBtn = new Border { Width = 18, Height = 18, CornerRadius = new CornerRadius(9), Margin = new Thickness(8, -2, -4, 0),
                                Child = closeGlyph, Opacity = 0, Cursor = Cursors.Arrow, ToolTip = "Dismiss", Background = Brushes.Transparent };
        closeBtn.MouseEnter += (s, e) => closeBtn.Background = Overlay(0x30);
        closeBtn.MouseLeave += (s, e) => closeBtn.Background = Brushes.Transparent;
        closeBtn.MouseLeftButtonUp += (s, e) => { e.Handled = true; Dismiss(); };
        DockPanel.SetDock(closeBtn, Dock.Right);
        whenText = new TextBlock { FontSize = 11 };
        DockPanel.SetDock(whenText, Dock.Right);
        moreText = new TextBlock { FontSize = 11, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 8, 0) };
        DockPanel.SetDock(moreText, Dock.Right);
        appText = new TextBlock { FontSize = 11, FontWeight = FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis };
        head.Children.Add(closeBtn);
        head.Children.Add(whenText);
        head.Children.Add(moreText);
        head.Children.Add(appText);
        titleText = new TextBlock { FontSize = 14.5, FontWeight = FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(0, 1, 0, 0) };
        bodyText = new TextBlock { FontSize = 12.5, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.Wrap, MaxHeight = 36 };
        text.Children.Add(head);
        text.Children.Add(titleText);
        text.Children.Add(bodyText);
        mainArea.MouseLeftButtonUp += (s, e) => Open();

        // ---- actions row (shown on hover)
        actionsRow = new Grid { Margin = new Thickness(72, -4, 16, 14), Visibility = Visibility.Collapsed };
        actionsRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        actionsRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        actionsRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        Grid.SetRow(actionsRow, 1);
        content.Children.Add(actionsRow);

        replyBox = new Border { Height = 30, CornerRadius = new CornerRadius(15), BorderThickness = new Thickness(1) };
        var rg = new Grid();
        replyHint = new TextBlock { Margin = new Thickness(13, 0, 8, 0), VerticalAlignment = VerticalAlignment.Center, FontSize = 12.5, IsHitTestVisible = false };
        reply = new TextBox { Background = Brushes.Transparent, BorderThickness = new Thickness(0), FontSize = 12.5,
                              VerticalContentAlignment = VerticalAlignment.Center, Padding = new Thickness(9, 0, 9, 0) };
        reply.TextChanged += (s, e) => replyHint.Visibility = reply.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        reply.PreviewMouseLeftButtonDown += (s, e) => StartTyping();
        reply.KeyDown += (s, e) => {
            if (e.Key == Key.Enter) { e.Handled = true; SendReply(); }
            else if (e.Key == Key.Escape) { e.Handled = true; Dismiss(); }
        };
        rg.Children.Add(replyHint);
        rg.Children.Add(reply);
        replyBox.Child = rg;
        actionsRow.Children.Add(replyBox);

        sendBtn = new Border { Width = 30, Height = 30, CornerRadius = new CornerRadius(15), Margin = new Thickness(6, 0, 0, 0), Cursor = Cursors.Hand, ToolTip = "Send",
            Child = new System.Windows.Shapes.Path { Data = Geometry.Parse("M0,5 L10,5 M5.5,0.5 L10,5 L5.5,9.5"), Stroke = Brushes.White, StrokeThickness = 1.8,
                StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round, StrokeLineJoin = PenLineJoin.Round,
                HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center } };
        sendBtn.MouseLeftButtonUp += (s, e) => { e.Handled = true; SendReply(); };
        Grid.SetColumn(sendBtn, 1);
        actionsRow.Children.Add(sendBtn);

        buttons = new StackPanel { Orientation = Orientation.Horizontal };
        Grid.SetColumn(buttons, 2);
        actionsRow.Children.Add(buttons);

        // ---- compact layout (size 0): pill high, one line
        compact = new Grid { Height = PillH - 2, Width = CompactW - 2, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top,
                             Opacity = 0, Visibility = Visibility.Collapsed, Background = Brushes.Transparent, Cursor = Cursors.Hand };
        compact.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        compact.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var cFace = new Grid { Width = 21, Height = 21, Margin = new Thickness(6, 0, 8, 0), VerticalAlignment = VerticalAlignment.Center };
        cAvatar = new Ellipse();
        cInitial = new TextBlock { FontSize = 10, FontWeight = FontWeights.SemiBold, Foreground = Brushes.White,
                                   HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        cFace.Children.Add(cAvatar);
        cFace.Children.Add(cInitial);
        cAppBadge = new Ellipse { Width = 9, Height = 9 };
        cAppBadgeRing = new Border { Width = 11, Height = 11, CornerRadius = new CornerRadius(5.5), Child = cAppBadge,
                                     HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom,
                                     Margin = new Thickness(0, 0, -2, -2), Visibility = Visibility.Collapsed };
        cFace.Children.Add(cAppBadgeRing);
        compact.Children.Add(cFace);
        cText = new TextBlock { FontSize = 12.5, VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(0, 0, 14, 0) };
        Grid.SetColumn(cText, 1);
        compact.Children.Add(cText);
        compact.MouseLeftButtonUp += (s, e) => Open();
        inner.Children.Add(compact);

        ApplyTheme();

        card.MouseEnter += (s, e) => {
            hovering = true;
            Fade(closeBtn, 0.85, 120);
            if (expanded && !collapsing && (hasExtras || size == 0) && !hoverOpen) { hoverOpen = true; Relayout(HoverMs); }
        };
        card.MouseLeave += (s, e) => {
            hovering = false;
            Fade(closeBtn, 0, 160);
            if (collapseAt < DateTime.Now.AddSeconds(1.5)) collapseAt = DateTime.Now.AddSeconds(1.5);
            if (hoverOpen && !typing && reply.Text.Length == 0 && expanded && !collapsing) { hoverOpen = false; Relayout(HoverMs); }
        };
        card.MouseRightButtonUp += (s, e) => Dismiss();
        Deactivated += (s, e) => {
            typing = false;
            NoActivate(true);
            if (!hovering && hoverOpen && reply.Text.Length == 0 && expanded && !collapsing) { hoverOpen = false; Relayout(HoverMs); }
        };

        tick = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
        int n = 0;
        tick.Tick += (s, e) => {
            if (!IsVisible) return;
            if (expanded && !collapsing && !hovering && !typing && DateTime.Now >= collapseAt) Collapse();
            if (++n % 10 == 0 && !typing) KeepOnTop(); // Fenn re-asserts its own topmost now and then
        };
        tick.Start();

        SourceInitialized += (s, e) => {
            hwnd = new WindowInteropHelper(this).Handle;
            NoActivate(true);
        };
    }

    // ------------------------------------------------------------------ window bits

    void NoActivate(bool on) {
        if (hwnd == IntPtr.Zero) return;
        int ex = GetWindowLong(hwnd, -20) | 0x80; // WS_EX_TOOLWINDOW: no taskbar or Alt+Tab entry
        ex = on ? ex | 0x08000000 : ex & ~0x08000000; // WS_EX_NOACTIVATE: never steal focus
        SetWindowLong(hwnd, -20, ex);
    }

    void KeepOnTop() {
        if (hwnd != IntPtr.Zero) SetWindowPos(hwnd, new IntPtr(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010);
    }

    void StartTyping() {
        typing = true;
        NoActivate(false);
        Activate();
        reply.Focus();
    }

    // ------------------------------------------------------------------ look

    static SolidColorBrush B(byte a, byte r, byte g, byte b) { var x = new SolidColorBrush(Color.FromArgb(a, r, g, b)); x.Freeze(); return x; }
    SolidColorBrush Overlay(byte a) { return dark ? B(a, 0xFF, 0xFF, 0xFF) : B(a, 0, 0, 0); }

    void ApplyTheme() {
        bool glassy = style != "solid";
        var strong = dark ? Brushes.White : B(0xFF, 0x15, 0x15, 0x1A);
        var dim = dark ? B(0xA8, 0xFF, 0xFF, 0xFF) : B(0x99, 0, 0, 0);
        appText.Foreground = dim; whenText.Foreground = dim; closeGlyph.Stroke = strong;
        titleText.Foreground = strong;
        bodyText.Foreground = dark ? B(0xDD, 0xFF, 0xFF, 0xFF) : B(0xCC, 0, 0, 0);
        moreText.Foreground = new SolidColorBrush(dark ? Lighten(accent, 0.25) : accent);
        cText.Foreground = strong;
        replyHint.Foreground = dim;
        reply.Foreground = strong; reply.CaretBrush = strong;
        replyBox.Background = Overlay(0x1C); replyBox.BorderBrush = Overlay(0x26);
        sendBtn.Background = new SolidColorBrush(accent);
        foreach (Border b in buttons.Children) StyleButton(b);

        backCanvas.Visibility = glassy ? Visibility.Visible : Visibility.Collapsed;
        ((BlurEffect)backdrop.Effect).Radius = style == "tinted" ? 40 : 26;
        sheen.Visibility = glassy ? Visibility.Visible : Visibility.Collapsed;
        if (!glassy) {
            tintBase.Background = dark ? B(0xFF, 0x14, 0x16, 0x1B) : B(0xFF, 0xF7, 0xF7, 0xFA);
            tintColor.Background = null;
            card.BorderBrush = dark ? B(0x2A, 0xFF, 0xFF, 0xFF) : B(0x22, 0, 0, 0);
            content.Effect = null; compact.Effect = null;
            return;
        }
        // Liquid glass: blurred desktop, a light tint, a top sheen and a bright rim.
        if (style == "tinted") {
            // Smoked glass: almost black, heavily blurred, a touch see-through, like the notch itself.
            byte ta = TintAlpha[Math.Max(0, Math.Min(3, tint))];
            tintBase.Background = dark ? B(ta, 0x0D, 0x0E, 0x12) : B(ta, 0xF3, 0xF3, 0xF6);
            tintColor.Background = null;
        } else {
            tintBase.Background = dark ? B(0x5C, 0x0C, 0x0E, 0x14) : B(0x8C, 0xFF, 0xFF, 0xFF);
            tintColor.Background = null;
        }
        bool smoked = style == "tinted";
        var sh = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(0, 1) };
        // A soft top-lit sheen that eases out (smoothstep-like), no hard band.
        double top = smoked ? 0x12 : 0x2C;
        double[] at = { 0, 0.12, 0.25, 0.4, 0.55, 0.7, 0.85, 1 };
        double[] k = { 1, 0.86, 0.62, 0.36, 0.17, 0.07, 0.03, 0.04 };
        for (int i = 0; i < at.Length; i++)
            sh.GradientStops.Add(new GradientStop(Color.FromArgb((byte)Math.Round(top * k[i]), 0xFF, 0xFF, 0xFF), at[i]));
        sheen.Background = sh;
        var rim = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 1) };
        rim.GradientStops.Add(new GradientStop(Color.FromArgb((byte)(smoked ? 0x48 : 0xA0), 0xFF, 0xFF, 0xFF), 0));
        rim.GradientStops.Add(new GradientStop(Color.FromArgb((byte)(smoked ? 0x1C : 0x22), 0xFF, 0xFF, 0xFF), 0.32));
        rim.GradientStops.Add(new GradientStop(Color.FromArgb((byte)(smoked ? 0x12 : 0x14), 0xFF, 0xFF, 0xFF), 0.68));
        rim.GradientStops.Add(new GradientStop(Color.FromArgb((byte)(smoked ? 0x30 : 0x70), 0xFF, 0xFF, 0xFF), 1));
        card.BorderBrush = rim;
        if (dark) {
            // A soft shadow keeps text readable on bright wallpapers.
            content.Effect = new DropShadowEffect { BlurRadius = 8, ShadowDepth = 0, Opacity = 0.45, Color = Colors.Black };
            compact.Effect = new DropShadowEffect { BlurRadius = 8, ShadowDepth = 0, Opacity = 0.45, Color = Colors.Black };
        } else { content.Effect = null; compact.Effect = null; }
    }

    static Color Lighten(Color c, double t) {
        return Color.FromRgb((byte)(c.R + (255 - c.R) * t), (byte)(c.G + (255 - c.G) * t), (byte)(c.B + (255 - c.B) * t));
    }

    void StyleButton(Border b) {
        b.Background = Overlay(0x24);
        b.BorderBrush = Overlay(0x22);
        ((TextBlock)b.Child).Foreground = dark ? Brushes.White : B(0xFF, 0x15, 0x15, 0x1A);
    }

    void Reshape() {
        double r = Math.Min(card.ActualHeight / 2, 26);
        bool notch = FennLayout.Notch;
        // Fenn's "Notch" shape hangs from the top edge: square top corners, no top rim.
        card.CornerRadius = notch ? new CornerRadius(0, 0, r, r) : new CornerRadius(r);
        card.BorderThickness = notch ? new Thickness(1, 0, 1, 1) : new Thickness(1);
        double iw = Math.Max(0, card.ActualWidth - 2), ih = Math.Max(0, card.ActualHeight - (notch ? 1 : 2));
        inner.Clip = RoundRect(iw, ih, notch ? 0 : r - 1, r - 1);
        // Keep the blurred snapshot lined up with the real desktop behind the card.
        // The snapshot carries a border of repeated edge pixels (padX/padY) so
        // the heavy blur never pulls in transparency at the edges.
        backdrop.Width = Width + padX * 2;
        backdrop.Height = Height + padY * 2;
        Canvas.SetLeft(backdrop, -CardLeft() - 1 - padX);
        Canvas.SetTop(backdrop, -(FennLayout.Notch ? 0 : 1) - padY);
    }

    double padX, padY;

    void CaptureBackdrop() {
        if (style == "solid") return;
        try {
            double dpi = FennLayout.Scale();
            int x = (int)Math.Round(Left * dpi), y = (int)Math.Round(Top * dpi);
            int w = (int)Math.Round(Width * dpi), h = (int)Math.Round(Height * dpi);
            using (var full = new System.Drawing.Bitmap(w, h)) {
                using (var g = System.Drawing.Graphics.FromImage(full)) g.CopyFromScreen(x, y, 0, 0, new System.Drawing.Size(w, h));
                // Shrinking the shot a lot is most of the blur; the BlurEffect smooths it out.
                int k = style == "tinted" ? 28 : 18, pad = 6;
                int sw = Math.Max(6, w / k), shh = Math.Max(4, h / k);
                using (var small = new System.Drawing.Bitmap(sw + pad * 2, shh + pad * 2)) {
                    using (var g = System.Drawing.Graphics.FromImage(small)) {
                        g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBilinear;
                        g.PixelOffsetMode = System.Drawing.Drawing2D.PixelOffsetMode.HighQuality;
                        g.DrawImage(full, new System.Drawing.Rectangle(pad, pad, sw, shh));
                    }
                    // Repeat the edge pixels into the border.
                    for (int yy = 0; yy < small.Height; yy++)
                        for (int xx = 0; xx < small.Width; xx++)
                            if (xx < pad || yy < pad || xx >= pad + sw || yy >= pad + shh)
                                small.SetPixel(xx, yy, small.GetPixel(Math.Min(pad + sw - 1, Math.Max(pad, xx)), Math.Min(pad + shh - 1, Math.Max(pad, yy))));
                    padX = pad * Width / sw;
                    padY = pad * Height / shh;
                    IntPtr hb = small.GetHbitmap();
                    try {
                        var src = Imaging.CreateBitmapSourceFromHBitmap(hb, IntPtr.Zero, Int32Rect.Empty, BitmapSizeOptions.FromEmptyOptions());
                        src.Freeze();
                        backdrop.Source = src;
                    } finally { DeleteObject(hb); }
                }
            }
        } catch { }
    }

    // ------------------------------------------------------------------ layout

    bool FullLayout { get { return size != 0 || hoverOpen; } }

    Size Target() {
        if (!FullLayout) return new Size(CompactW, PillH);
        double s = Scales[size];
        actionsRow.Visibility = hoverOpen && hasExtras ? Visibility.Visible : Visibility.Collapsed;
        content.LayoutTransform = new ScaleTransform(s, s);
        content.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        return new Size(Math.Round(Wide * s), Math.Ceiling(content.DesiredSize.Height) + 2);
    }

    void ShowLayout() {
        bool full = FullLayout;
        content.Visibility = full ? Visibility.Visible : Visibility.Collapsed;
        compact.Visibility = full ? Visibility.Collapsed : Visibility.Visible;
    }

    void Relayout(int ms) {
        bool wasFull = content.Visibility == Visibility.Visible;
        ShowLayout();
        var t = Target();
        Grow(card, t.Width, t.Height, ms, new CubicEase { EasingMode = EasingMode.EaseOut });
        if (wasFull != FullLayout) {
            UIElement shown = FullLayout ? (UIElement)content : compact;
            shown.Opacity = 0;
            Fade(shown, 1, 200, 60);
        }
    }

    // ------------------------------------------------------------------ show / hide

    public void Pop(string id, string app, string title, string body, string image, int more, double seconds, string when, string hint, string[] labels, string badge) {
        current = id;
        appText.Text = app;
        whenText.Text = when;
        moreText.Text = more > 0 ? "+" + more + " more" : "";
        titleText.Text = string.IsNullOrEmpty(title) ? app : title;
        bodyText.Text = body ?? "";
        bodyText.Visibility = string.IsNullOrEmpty(body) ? Visibility.Collapsed : Visibility.Visible;
        cText.Inlines.Clear();
        cText.Inlines.Add(new Run(string.IsNullOrEmpty(title) ? app : title) { FontWeight = FontWeights.SemiBold });
        if (!string.IsNullOrEmpty(body)) cText.Inlines.Add(new Run("  " + body.Replace("\n", " ")) { Foreground = bodyText.Foreground });
        if (more > 0) cText.Inlines.Add(new Run("  +" + more) { Foreground = moreText.Foreground, FontWeight = FontWeights.SemiBold });
        SetAvatar(image, app);
        var badgeBrush = LoadBrush(badge, 48);
        appBadgeRing.Visibility = cAppBadgeRing.Visibility = badgeBrush != null ? Visibility.Visible : Visibility.Collapsed;
        appBadge.Fill = cAppBadge.Fill = badgeBrush;
        appBadgeRing.Background = cAppBadgeRing.Background = dark ? B(0xFF, 0x16, 0x17, 0x1C) : B(0xFF, 0xF4, 0xF4, 0xF6);

        // Reply box and the toast's own buttons, revealed on hover.
        replyBox.Visibility = string.IsNullOrEmpty(hint) ? Visibility.Collapsed : Visibility.Visible;
        sendBtn.Visibility = replyBox.Visibility;
        replyHint.Text = hint;
        reply.Text = "";
        buttons.Children.Clear();
        for (int i = 0; i < labels.Length; i++) {
            if (labels[i].Length == 0) continue;
            int index = i;
            var b = new Border { CornerRadius = new CornerRadius(15), Height = 30, Padding = new Thickness(13, 0, 13, 0), Margin = new Thickness(6, 0, 0, 0),
                                 BorderThickness = new Thickness(1), Cursor = Cursors.Hand,
                                 Child = new TextBlock { Text = labels[i], FontSize = 12, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center } };
            StyleButton(b);
            b.MouseEnter += (s, e) => b.Background = Overlay(0x3A);
            b.MouseLeave += (s, e) => b.Background = Overlay(0x24);
            b.MouseLeftButtonUp += (s, e) => { e.Handled = true; Say("action|" + current + "|" + index); Collapse(); };
            buttons.Children.Add(b);
        }
        hasExtras = replyBox.Visibility == Visibility.Visible || buttons.Children.Count > 0;
        collapseAt = DateTime.Now.AddSeconds(Math.Max(2, seconds));

        if (expanded && !collapsing) {
            // Already open: a small bounce and a cross-fade to the new message.
            ShowLayout();
            var t2 = Target();
            Grow(card, t2.Width, t2.Height, 300, new BackEase { Amplitude = 0.5, EasingMode = EasingMode.EaseOut });
            UIElement shown = FullLayout ? (UIElement)content : compact;
            shown.Opacity = 0.15;
            Fade(shown, 1, 220);
            return;
        }
        collapsing = false;
        expanded = true;
        hoverOpen = false;
        typing = false;
        Place();
        CaptureBackdrop();
        card.BeginAnimation(WidthProperty, null);
        card.BeginAnimation(HeightProperty, null);
        card.Width = PillW; card.Height = PillH;
        ShowLayout();
        content.BeginAnimation(OpacityProperty, null); content.Opacity = 0;
        compact.BeginAnimation(OpacityProperty, null); compact.Opacity = 0;
        BeginAnimation(OpacityProperty, null);
        Opacity = 1;
        if (!IsVisible) Show();
        KeepOnTop();
        HookMouse(true);
        var t = Target();
        if (FennLayout.AutoHide) {
            // Fenn's notch is tucked away, so there's no pill to grow out of:
            // slide the whole bubble down from the top edge, like Fenn does.
            card.Width = t.Width; card.Height = t.Height;
            (FullLayout ? (UIElement)content : compact).Opacity = 1;
            slide.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(-(t.Height + 40), 0, TimeSpan.FromMilliseconds(GrowMs)) {
                EasingFunction = new BackEase { Amplitude = 0.25, EasingMode = EasingMode.EaseOut } });
        } else {
            slide.BeginAnimation(TranslateTransform.YProperty, null);
            slide.Y = 0;
            Grow(card, t.Width, t.Height, GrowMs, new BackEase { Amplitude = size == 0 ? 0.2 : 0.32, EasingMode = EasingMode.EaseOut });
            Fade(FullLayout ? (UIElement)content : compact, 1, 260, 170);
        }
    }

    static ImageBrush LoadBrush(string path, int px) {
        if (string.IsNullOrEmpty(path) || !File.Exists(path)) return null;
        try {
            var bmp = new BitmapImage();
            bmp.BeginInit();
            bmp.CacheOption = BitmapCacheOption.OnLoad;
            bmp.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
            bmp.DecodePixelWidth = px;
            bmp.UriSource = new Uri(path);
            bmp.EndInit();
            bmp.Freeze();
            return new ImageBrush(bmp) { Stretch = Stretch.UniformToFill };
        } catch { return null; }
    }

    void SetAvatar(string path, string app) {
        initial.Text = cInitial.Text = "";
        var brush = LoadBrush(path, 128);
        if (brush != null) { avatar.Fill = brush; cAvatar.Fill = brush; return; }
        avatar.Fill = cAvatar.Fill = new SolidColorBrush(accent);
        initial.Text = cInitial.Text = string.IsNullOrEmpty(app) ? "?" : app.Substring(0, 1).ToUpper();
    }

    void Open() {
        if (!expanded || collapsing) return;
        Say("click|" + current);
        Collapse();
    }

    void SendReply() {
        string t = reply.Text.Trim();
        if (t.Length == 0) return;
        Say("reply|" + current + "|" + Convert.ToBase64String(Encoding.UTF8.GetBytes(t)));
        Collapse();
    }

    public void Dismiss() {
        if (!expanded || collapsing) return;
        Say("dismiss|" + current);
        Collapse();
    }

    public void Collapse() {
        if (!expanded || collapsing) return;
        collapsing = true;
        typing = false;
        NoActivate(true);
        Fade(closeBtn, 0, 100);
        if (FennLayout.AutoHide) {
            var up = new DoubleAnimation(-(card.ActualHeight + 40), TimeSpan.FromMilliseconds(ShrinkMs)) {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseIn } };
            up.Completed += (s, e) => {
                if (!collapsing) return;
                collapsing = false; expanded = false; hoverOpen = false;
                HookMouse(false);
                Hide();
                slide.BeginAnimation(TranslateTransform.YProperty, null);
                slide.Y = 0;
                Say("hidden|" + current);
            };
            slide.BeginAnimation(TranslateTransform.YProperty, up);
            return;
        }
        Fade(content, 0, 140);
        Fade(compact, 0, 140);
        Fade(closeBtn, 0, 100);
        var ease = new CubicEase { EasingMode = EasingMode.EaseInOut };
        var w = new DoubleAnimation(PillW, TimeSpan.FromMilliseconds(ShrinkMs)) { EasingFunction = ease, BeginTime = TimeSpan.FromMilliseconds(60) };
        var h = new DoubleAnimation(PillH, TimeSpan.FromMilliseconds(ShrinkMs)) { EasingFunction = ease, BeginTime = TimeSpan.FromMilliseconds(60) };
        h.Completed += (s, e) => {
            if (!collapsing) return;
            // Melt into Fenn's own pill.
            var f = new DoubleAnimation(0, TimeSpan.FromMilliseconds(140));
            f.Completed += (s2, e2) => {
                if (!collapsing) return;
                collapsing = false; expanded = false; hoverOpen = false;
                HookMouse(false);
                Hide();
                Say("hidden|" + current);
            };
            BeginAnimation(OpacityProperty, f);
        };
        card.BeginAnimation(WidthProperty, w);
        card.BeginAnimation(HeightProperty, h);
    }

    static void Grow(FrameworkElement el, double w, double h, int ms, IEasingFunction ease) {
        el.BeginAnimation(WidthProperty, new DoubleAnimation(w, TimeSpan.FromMilliseconds(ms)) { EasingFunction = ease });
        el.BeginAnimation(HeightProperty, new DoubleAnimation(h, TimeSpan.FromMilliseconds(ms)) { EasingFunction = ease });
    }

    static void Fade(UIElement el, double to, int ms, int delay = 0) {
        el.BeginAnimation(OpacityProperty, new DoubleAnimation(to, TimeSpan.FromMilliseconds(ms)) {
            BeginTime = TimeSpan.FromMilliseconds(delay), EasingFunction = new QuadraticEase { EasingMode = EasingMode.EaseOut } });
    }

    // ------------------------------------------------------------------ protocol

    static readonly object outLock = new object();
    public static void Say(string line) {
        lock (outLock) { Console.Out.WriteLine(line); Console.Out.Flush(); }
    }

    static string D(string b64) {
        if (string.IsNullOrEmpty(b64)) return "";
        try { return Encoding.UTF8.GetString(Convert.FromBase64String(b64)); } catch { return ""; }
    }

    public void Handle(string line) {
        var f = line.Split('|');
        switch (f[0]) {
            case "theme":
                if (f.Length >= 3) {
                    dark = f[1] == "dark";
                    try { accent = (Color)ColorConverter.ConvertFromString(f[2]); } catch { }
                    ApplyTheme();
                }
                break;
            case "config":
                if (f.Length >= 3) {
                    style = f[1] == "glass" || f[1] == "tinted" ? f[1] : "solid";
                    int sz; if (int.TryParse(f[2], out sz)) size = Math.Max(0, Math.Min(4, sz));
                    int tl; if (f.Length >= 4 && int.TryParse(f[3], out tl)) tint = Math.Max(0, Math.Min(3, tl));
                    ApplyTheme();
                }
                break;
            case "show":
                if (f.Length >= 9) {
                    int more; int.TryParse(f[6], out more);
                    double secs; double.TryParse(f[7], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out secs);
                    string hint = f.Length > 9 ? D(f[9]) : "";
                    string[] labels = f.Length > 10 && f[10].Length > 0 ? D(f[10]).Split((char)0x1F) : new string[0];
                    Pop(f[1], D(f[2]), D(f[3]), D(f[4]), D(f[5]), more, secs, D(f[8]), hint, labels, f.Length > 11 ? D(f[11]) : "");
                }
                break;
            case "layout":
                // layout|<left|center|right>|<primary|secondary|cursor>|<margin>|<pill|notch>|<autohide 0/1>
                if (f.Length >= 6) {
                    FennLayout.Placement = f[1] == "left" || f[1] == "right" ? f[1] : "center";
                    FennLayout.MonitorMode = f[2] == "secondary" || f[2] == "cursor" ? f[2] : "primary";
                    double mg;
                    if (double.TryParse(f[3], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out mg))
                        FennLayout.Margin = Math.Max(0, Math.Min(64, mg));
                    FennLayout.Notch = f[4] == "notch";
                    FennLayout.AutoHide = f[5] == "1";
                    if (marker != null) marker.Relayout();
                    if (!IsOut) Place();
                    Reshape();
                }
                break;
            case "marker":
                if (marker != null && f.Length >= 3) {
                    int count; int.TryParse(f[2], out count);
                    marker.Set(f[1], count, accent, dark);
                }
                break;
            case "hide":
                Collapse();
                break;
            case "quit":
                Application.Current.Shutdown();
                break;
        }
    }

    public static void Run() {
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var bubble = new Bubble();
        bubble.marker = new Marker(bubble);
        var reader = new Thread(() => {
            string line;
            try {
                while ((line = Console.In.ReadLine()) != null) {
                    string l = line.Trim();
                    if (l.Length > 0) app.Dispatcher.BeginInvoke(new Action(() => bubble.Handle(l)));
                }
            } catch { }
            app.Dispatcher.BeginInvoke(new Action(() => app.Shutdown()));
        });
        reader.IsBackground = true;
        reader.Start();
        app.Startup += (s, e) => Say("ready");
        app.Run();
    }
}

// Where Fenn's notch is, from Fenn's own settings (sent by main.py from
// config.toml). Fenn spans its window across the top of the chosen monitor's
// work area and rests a 140 x 34 pill in it: left, centre or right, `margin`
// away from the edges. A "notch" shape hangs from the top edge instead.
public static class FennLayout {
    public static string Placement = "center", MonitorMode = "primary";
    public static double Margin = 0;
    public static bool Notch = false, AutoHide = false;
    public const double PillW = 140, PillH = 34;

    [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct MONITORINFO { public int Size; public RECT Monitor, Work; public uint Flags; }
    delegate bool MonitorProc(IntPtr mon, IntPtr hdc, IntPtr rect, IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorProc proc, IntPtr data);
    [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr mon, ref MONITORINFO info);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromPoint(POINT p, uint flags);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);

    /// Screen pixels per DIP in this process's coordinate space.
    public static double Scale() {
        double px = GetSystemMetrics(0); // SM_CXSCREEN
        return px > 0 ? px / SystemParameters.PrimaryScreenWidth : 1.0;
    }

    static MONITORINFO Info(IntPtr mon) {
        var mi = new MONITORINFO { Size = Marshal.SizeOf(typeof(MONITORINFO)) };
        GetMonitorInfo(mon, ref mi);
        return mi;
    }

    /// The work area of the monitor Fenn uses, in DIPs.
    public static Rect Work() {
        MONITORINFO pick = new MONITORINFO();
        bool found = false;
        if (MonitorMode == "cursor") {
            POINT p; GetCursorPos(out p);
            pick = Info(MonitorFromPoint(p, 2)); // nearest
            found = true;
        } else {
            MONITORINFO primary = new MONITORINFO(), other = new MONITORINFO();
            bool hasPrimary = false, hasOther = false;
            EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (m, h, r, d) => {
                var mi = Info(m);
                if ((mi.Flags & 1) != 0) { primary = mi; hasPrimary = true; }
                else if (!hasOther) { other = mi; hasOther = true; }
                return true;
            }, IntPtr.Zero);
            if (MonitorMode == "secondary" && hasOther) { pick = other; found = true; }
            else if (hasPrimary) { pick = primary; found = true; }
        }
        double s = Scale();
        if (!found) return new Rect(0, 0, SystemParameters.PrimaryScreenWidth, SystemParameters.PrimaryScreenHeight);
        return new Rect(pick.Work.L / s, pick.Work.T / s, (pick.Work.R - pick.Work.L) / s, (pick.Work.B - pick.Work.T) / s);
    }

    /// Fenn's collapsed pill, in DIPs (screen coordinates).
    public static Rect Pill() {
        var work = Work();
        double frac = Placement == "left" ? 0 : Placement == "right" ? 1 : 0.5;
        double lo = work.Left + Margin, hi = Math.Max(lo, work.Right - Margin - PillW);
        double x = lo + (hi - lo) * frac;
        double y = work.Top + (Notch ? 0 : Margin);
        return new Rect(x, y, PillW, PillH);
    }
}

// The unread marker: a small dot or count drawn in the gap between the time
// and the date of Fenn's collapsed clock pill. Click-through, never focused.
// It finds the gap on screen, so it follows the pill wherever Fenn puts it.
public class Marker : Window {
    readonly Bubble bubble;
    readonly Grid holder;
    readonly Ellipse dot;
    readonly Border count;
    readonly TextBlock countText;
    readonly ScaleTransform pop = new ScaleTransform(1, 1);
    readonly DispatcherTimer tick;
    string style = "none";
    bool dark = true, shown;
    double? gapX;          // centre of the time/date gap in DIPs, when the clock shows
    int n, checkIn;
    IntPtr hwnd;

    [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
    [DllImport("user32.dll")] static extern int SetWindowLong(IntPtr h, int i, int v);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);

    public Marker(Bubble owner) {
        bubble = owner;
        WindowStyle = WindowStyle.None; AllowsTransparency = true; Background = Brushes.Transparent;
        Topmost = true; ShowInTaskbar = false; ShowActivated = false; ResizeMode = ResizeMode.NoResize;
        Width = 30; Height = 30; Top = 0; Left = 0;
        Opacity = 0;
        holder = new Grid { HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Height = 16,
                            RenderTransformOrigin = new Point(0.5, 0.5), RenderTransform = pop };
        dot = new Ellipse { Width = 7, Height = 7, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        countText = new TextBlock { FontSize = 9.5, FontWeight = FontWeights.Bold, Foreground = Brushes.White,
                                    HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
                                    Margin = new Thickness(0, 0, 0, 0.5) };
        count = new Border { MinWidth = 15, Height = 15, CornerRadius = new CornerRadius(7.5), Padding = new Thickness(3, 0, 3, 0),
                             HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Child = countText };
        holder.Children.Add(dot);
        holder.Children.Add(count);
        Content = holder;
        TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display);
        SourceInitialized += (s, e) => {
            hwnd = new WindowInteropHelper(this).Handle;
            // no activation, no taskbar entry, and clicks fall through to Fenn
            SetWindowLong(hwnd, -20, GetWindowLong(hwnd, -20) | 0x08000000 | 0x80 | 0x20);
        };
        tick = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(150) };
        int ticks = 0;
        tick.Tick += (s, e) => {
            Update();
            ticks++;
            // Fenn re-raises its own full-width window now and then; stay above it.
            if (shown && hwnd != IntPtr.Zero && ticks % 4 == 0) SetWindowPos(hwnd, new IntPtr(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010);
            if (Environment.GetEnvironmentVariable("FENN_MARKER_DEBUG") == "1" && ticks % 7 == 0)
                Bubble.Say("debug|marker shown=" + shown + " style=" + style + " n=" + n + " gap=" + gapX + " pill=" + FennLayout.Pill());
        };
        tick.Start();
    }

    public void Set(string newStyle, int number, Color accent, bool isDark) {
        bool grew = number > n;
        style = newStyle == "dot" || newStyle == "count" ? newStyle : "none";
        n = number; dark = isDark;
        var fill = new SolidColorBrush(accent);
        dot.Fill = fill;
        dot.Effect = new DropShadowEffect { BlurRadius = 6, ShadowDepth = 0, Opacity = 0.9, Color = accent };
        count.Background = fill;
        countText.Text = n > 99 ? "99+" : n.ToString();
        dot.Visibility = style == "dot" ? Visibility.Visible : Visibility.Collapsed;
        count.Visibility = style == "count" ? Visibility.Visible : Visibility.Collapsed;
        checkIn = 0;
        if (shown && grew) Pop(1.3); // a new message while it's showing: small bump
        Update();
    }

    /// Fenn's settings changed: look for the pill again right away.
    public void Relayout() { checkIn = 0; }

    bool Wanted() {
        // Tucked-away notch: there is no pill to put it on.
        if (style == "none" || n <= 0 || bubble.IsOut || FennLayout.AutoHide) return false;
        double scale = FennLayout.Scale();
        var pill = FennLayout.Pill();
        POINT p; GetCursorPos(out p);
        double cx = p.X / scale, cy = p.Y / scale;
        // Fenn grows under the cursor.
        if (cx > pill.Left - 50 && cx < pill.Right + 50 && cy < pill.Bottom + 36) return false;
        if (--checkIn <= 0) { checkIn = 5; gapX = FindGap(pill, scale); }
        return gapX.HasValue;
    }

    bool IsText(System.Drawing.Color c) {
        int hi = Math.Max(c.R, Math.Max(c.G, c.B)), lo = Math.Min(c.R, Math.Min(c.G, c.B));
        return dark ? (lo > 140 && hi - lo < 50) : (hi < 95 && hi - lo < 50);
    }

    // The centre of the empty gap between the time and the date, in DIPs, or
    // null when the pill isn't showing its clock (music, open notch, ...).
    double? FindGap(Rect pill, double scale) {
        try {
            int x0 = (int)Math.Round((pill.Left + 6) * scale), w = (int)Math.Round((pill.Width - 12) * scale);
            int y0 = (int)Math.Round((pill.Top + 7) * scale), h = (int)Math.Round((pill.Height - 14) * scale);
            if (w < 10 || h < 4) return null;
            var cols = new bool[w];
            // Our own marker sits in the gap: don't mistake it for text.
            int skipA = int.MaxValue, skipB = int.MinValue;
            if (shown) { skipA = (int)((Left - 2) * scale) - x0; skipB = (int)((Left + Width + 2) * scale) - x0; }
            using (var bmp = new System.Drawing.Bitmap(w, h)) {
                using (var g = System.Drawing.Graphics.FromImage(bmp)) g.CopyFromScreen(x0, y0, 0, 0, new System.Drawing.Size(w, h));
                for (int x = 0; x < w; x++) {
                    if (x >= skipA && x <= skipB) continue;
                    for (int y = 0; y < h; y++) if (IsText(bmp.GetPixel(x, y))) { cols[x] = true; break; }
                }
            }
            // Runs of text columns; letters and words closer than 7 DIP belong together.
            var runs = new System.Collections.Generic.List<int[]>();
            int join = (int)(7 * scale);
            for (int x = 0; x < w; x++) {
                if (!cols[x]) continue;
                if (runs.Count > 0 && x - runs[runs.Count - 1][1] <= join) runs[runs.Count - 1][1] = x;
                else runs.Add(new[] { x, x });
            }
            if (runs.Count < 2) return null;
            int best = -1, bestW = 0;
            for (int i = 0; i + 1 < runs.Count; i++) {
                int gw = runs[i + 1][0] - runs[i][1];
                if (gw > bestW) { bestW = gw; best = i; }
            }
            if (bestW < 16 * scale) return null;
            return (x0 + (runs[best][1] + runs[best + 1][0]) / 2.0) / scale;
        } catch { return null; }
    }

    void Update() {
        bool want = Wanted();
        if (want && gapX.HasValue) {
            var pill = FennLayout.Pill();
            Left = gapX.Value - Width / 2;
            Top = pill.Top + pill.Height / 2 - Height / 2;
        }
        if (want == shown) return;
        shown = want;
        if (want) {
            if (!IsVisible) Show();
            if (hwnd != IntPtr.Zero) SetWindowPos(hwnd, new IntPtr(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010);
            BeginAnimation(OpacityProperty, new DoubleAnimation(1, TimeSpan.FromMilliseconds(220)));
            Pop(0.4);
        } else {
            var f = new DoubleAnimation(0, TimeSpan.FromMilliseconds(160));
            f.Completed += (s, e) => { if (!shown) Hide(); };
            BeginAnimation(OpacityProperty, f);
        }
    }

    void Pop(double from) {
        var a = new DoubleAnimation(from, 1, TimeSpan.FromMilliseconds(380)) { EasingFunction = new BackEase { Amplitude = 0.5, EasingMode = EasingMode.EaseOut } };
        pop.BeginAnimation(ScaleTransform.ScaleXProperty, a);
        pop.BeginAnimation(ScaleTransform.ScaleYProperty, a);
    }
}
}
'@
[FennNotify.Bubble]::Run()
