#!/usr/bin/env python3
"""
Calendar Post-It
~~~~~~~~~~~~~~~~
Polls Google Calendar every 60 seconds and pops up a yellow sticky-note
10 minutes before each upcoming meeting.  Runs as a macOS LaunchAgent.

Place credentials.json (downloaded from Google Cloud Console) in the same
folder as this script, then run install.sh.
"""

import sys
import platform
import ctypes
import ctypes.util
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame,
    QSystemTrayIcon, QMenu,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import QFont, QColor, QPixmap, QPainter, QIcon, QBrush, QPen

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Configuration ──────────────────────────────────────────────────────────────
SCRIPT_DIR       = Path(__file__).parent.resolve()
CREDENTIALS_FILE = SCRIPT_DIR / "credentials.json"
TOKEN_FILE       = SCRIPT_DIR / "token.json"
SCOPES           = ["https://www.googleapis.com/auth/calendar.readonly"]

ALERT_MINUTES    = 10         # show popup this many minutes before meeting
POLL_INTERVAL_MS = 60_000     # check calendar every 60 s

# ── Colours ────────────────────────────────────────────────────────────────────
YELLOW_BG        = "#FFE84D"
YELLOW_STRIPE    = "#F5C800"
YELLOW_BTN       = "#F5C800"
YELLOW_BTN_HOVER = "#E6B800"
YELLOW_BTN_DOWN  = "#CC9F00"
TEXT_DARK        = "#2c2000"
TEXT_MED         = "#5a4a00"

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "calendar_postit.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Google Calendar helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_calendar_service():
    """Return an authorised Google Calendar API service, refreshing if needed."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_FILE}\n"
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_upcoming_events(service, window_minutes: int = ALERT_MINUTES + 2):
    """Return timed events (not all-day) starting within the alert window.

    Queries ALL user calendars (not just primary) so meetings on shared,
    work, or secondary calendars are included.
    Looks back ALERT_MINUTES into the past so meetings that have already
    started (but not yet been shown) still trigger a popup.
    """
    now      = datetime.now(timezone.utc)
    time_min = now - timedelta(minutes=ALERT_MINUTES)
    time_max = now + timedelta(minutes=window_minutes)

    # Collect all writable/readable calendars
    cal_list = service.calendarList().list().execute()
    calendar_ids = []
    for cal in cal_list.get("items", []):
        role     = cal.get("accessRole", "")
        selected = cal.get("selected", True)
        name     = cal.get("summary", cal["id"])
        log.info(f"  Calendar: {name!r}  role={role}  selected={selected}")
        if selected and role in ("owner", "writer", "reader"):
            calendar_ids.append(cal["id"])

    events = []
    seen_ids = set()
    for cal_id in calendar_ids:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            for ev in result.get("items", []):
                if ev["id"] not in seen_ids and "dateTime" in ev.get("start", {}):
                    events.append(ev)
                    seen_ids.add(ev["id"])
        except Exception as exc:
            log.warning(f"Could not fetch calendar {cal_id!r}: {exc}")

    return events


# ══════════════════════════════════════════════════════════════════════════════
# Post-It Widget
# ══════════════════════════════════════════════════════════════════════════════

class PostItNote(QWidget):
    """A frameless, always-on-top yellow sticky-note for one calendar event."""

    acknowledged = pyqtSignal(str)   # emits event_id when dismissed

    def __init__(self, event: dict, stack_index: int = 0):
        super().__init__()
        self.event_id    = event["id"]
        self.event       = event
        self.stack_index = stack_index
        self._drag_pos   = None

        raw = event["start"]["dateTime"]
        self.start_time = datetime.fromisoformat(raw)
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)

        self._init_window()
        self._build_ui()
        self.adjustSize()
        self._reposition()

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_countdown)
        self._tick.start(1000)
        self._update_countdown()

        # Force on top once the native window handle exists, then every 3 s
        QTimer.singleShot(150, self._force_on_top)
        self._top_timer = QTimer(self)
        self._top_timer.timeout.connect(self._force_on_top)
        self._top_timer.start(3000)

    # ── Window flags ──────────────────────────────────────────────────────────

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint  |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(340)

    # ── True always-on-top via macOS NSWindow level ───────────────────────────

    def _force_on_top(self):
        """Raise the window and, on macOS, set NSWindow level above all apps."""
        self.raise_()
        if platform.system() == "Darwin":
            self._set_ns_window_level(25)   # NSStatusWindowLevel = 25

    def _set_ns_window_level(self, level: int):
        """Call [NSWindow setLevel:] via the ObjC runtime — no extra packages needed."""
        try:
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

            objc.sel_registerName.restype  = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_msgSend.restype      = ctypes.c_void_p
            objc.objc_msgSend.argtypes     = [ctypes.c_void_p, ctypes.c_void_p]

            ns_view   = ctypes.c_void_p(int(self.winId()))
            sel_win   = objc.sel_registerName(b"window")
            ns_window = ctypes.c_void_p(objc.objc_msgSend(ns_view, sel_win))

            sel_level = objc.sel_registerName(b"setLevel:")
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
            objc.objc_msgSend(ns_window, sel_level, level)
        except Exception as exc:
            log.debug(f"NSWindow setLevel failed: {exc}")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Root layout – extra margins leave room for the drop shadow
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 12, 12)

        # ── Yellow card ──
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(f"""
            QFrame#card {{
                background-color: {YELLOW_BG};
                border-radius: 4px;
                border-top: 5px solid {YELLOW_STRIPE};
            }}
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(3, 5)
        card.setGraphicsEffect(shadow)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 16)
        cl.setSpacing(7)

        # ── Header: pin + label ──
        hrow = QHBoxLayout()
        pin = QLabel("📌")
        pin.setFont(QFont("Apple Color Emoji", 13))
        hdr = QLabel("Upcoming Meeting")
        hdr.setFont(QFont("Helvetica Neue", 10, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {TEXT_MED};")
        hrow.addWidget(pin)
        hrow.addWidget(hdr)
        hrow.addStretch()
        cl.addLayout(hrow)

        # ── Hairline divider ──
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background-color: {YELLOW_STRIPE};")
        line.setFixedHeight(1)
        cl.addWidget(line)

        # ── Meeting title ──
        title_lbl = QLabel(self.event.get("summary", "Untitled Meeting"))
        title_lbl.setFont(QFont("Helvetica Neue", 14, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {TEXT_DARK};")
        title_lbl.setWordWrap(True)
        cl.addWidget(title_lbl)

        # ── Start time ──
        local_start = self.start_time.astimezone()
        time_str    = local_start.strftime("%-I:%M %p")
        t_lbl = QLabel(f"🕐  {time_str}")
        t_lbl.setFont(QFont("Helvetica Neue", 11))
        t_lbl.setStyleSheet(f"color: {TEXT_MED};")
        cl.addWidget(t_lbl)

        # ── Location (optional) ──
        location = self.event.get("location", "").strip()
        if location:
            loc_lbl = QLabel(f"📍  {location}")
            loc_lbl.setFont(QFont("Helvetica Neue", 10))
            loc_lbl.setStyleSheet(f"color: {TEXT_MED};")
            loc_lbl.setWordWrap(True)
            cl.addWidget(loc_lbl)

        # ── Video link (optional) ──
        video_uri = self._get_video_uri()
        if video_uri:
            vid_lbl = QLabel(
                f'🎥  <a href="{video_uri}" style="color:{TEXT_MED};">Join video call</a>'
            )
            vid_lbl.setFont(QFont("Helvetica Neue", 10))
            vid_lbl.setOpenExternalLinks(True)
            cl.addWidget(vid_lbl)

        # ── Countdown ──
        self.countdown_lbl = QLabel("")
        self.countdown_lbl.setFont(QFont("Helvetica Neue", 28, QFont.Weight.Bold))
        self.countdown_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_lbl.setStyleSheet(f"color: {TEXT_DARK}; margin: 6px 0 4px 0;")
        cl.addWidget(self.countdown_lbl)

        # ── Acknowledge button ──
        btn = QPushButton("Got it  ✓")
        btn.setFont(QFont("Helvetica Neue", 11, QFont.Weight.Medium))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(38)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {YELLOW_BTN};
                color: {TEXT_DARK};
                border: none;
                border-radius: 6px;
                padding: 0 20px;
            }}
            QPushButton:hover   {{ background-color: {YELLOW_BTN_HOVER}; }}
            QPushButton:pressed {{ background-color: {YELLOW_BTN_DOWN};  }}
        """)
        btn.clicked.connect(self._acknowledge)
        cl.addWidget(btn)

        root.addWidget(card)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_video_uri(self):
        conf = self.event.get("conferenceData", {})
        for ep in conf.get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                return ep.get("uri")
        return None

    def _reposition(self):
        screen = QApplication.primaryScreen().availableGeometry()
        h = max(self.sizeHint().height(), 250)
        x = screen.right() - self.width() - 24
        y = screen.bottom() - h - 24 - self.stack_index * (h + 14)
        self.move(x, max(screen.top() + 10, y))

    # ── Countdown update ──────────────────────────────────────────────────────

    def _update_countdown(self):
        now  = datetime.now(timezone.utc)
        secs = int((self.start_time - now).total_seconds())

        if secs <= 0:
            self.countdown_lbl.setText("🔔 Starting now!")
            self.countdown_lbl.setStyleSheet(
                "color: #cc0000; font-size: 18px; margin: 6px 0 4px 0;"
            )
        elif secs < 60:
            self.countdown_lbl.setText(f"⏱  {secs}s")
            self.countdown_lbl.setStyleSheet(
                "color: #c05000; margin: 6px 0 4px 0;"
            )
        else:
            m, s = divmod(secs, 60)
            self.countdown_lbl.setText(f"⏱  {m}m {s:02d}s")
            self.countdown_lbl.setStyleSheet(
                f"color: {TEXT_DARK}; margin: 6px 0 4px 0;"
            )

    # ── Acknowledge ───────────────────────────────────────────────────────────

    def _acknowledge(self):
        self._tick.stop()
        self._top_timer.stop()
        self.acknowledged.emit(self.event_id)
        self.close()

    # ── Drag to reposition ────────────────────────────────────────────────────

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if ev.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, ev):
        self._drag_pos = None


# ══════════════════════════════════════════════════════════════════════════════
# Calendar Monitor
# ══════════════════════════════════════════════════════════════════════════════

class CalendarMonitor:
    """Polls Google Calendar and spawns PostItNote popups before meetings."""

    def __init__(self):
        self._notified: set[str] = set()          # event IDs already shown
        self._active:   dict[str, PostItNote] = {}

        # Validate credentials once at startup
        try:
            get_calendar_service()
            log.info("✓ Google Calendar connected")
        except Exception as exc:
            log.error(f"Calendar auth failed on startup: {exc}")

        self._poll = QTimer()
        self._poll.timeout.connect(self._check)
        self._poll.start(POLL_INTERVAL_MS)
        QTimer.singleShot(800, self._check)       # fire once immediately

    def _check(self):
        # Build a fresh service + HTTP connection every poll so newly-added
        # events are always visible (avoids stale connection-level caching).
        now = datetime.now(timezone.utc)
        log.info(f"Poll at {now.astimezone().strftime('%H:%M:%S')} — window: "
                 f"{(now - timedelta(minutes=ALERT_MINUTES)).astimezone().strftime('%H:%M')} "
                 f"→ {(now + timedelta(minutes=ALERT_MINUTES + 2)).astimezone().strftime('%H:%M')}")

        try:
            service = get_calendar_service()
        except Exception as exc:
            log.error(f"Auth failed: {exc}")
            return

        try:
            events = fetch_upcoming_events(service)
        except Exception as exc:
            log.error(f"Fetch failed (will retry next poll): {exc}")
            return

        log.info(f"  Found {len(events)} event(s) in window"
                 + (": " + ", ".join(f"{e.get('summary','?')!r} @ "
                    f"{datetime.fromisoformat(e['start']['dateTime']).astimezone().strftime('%H:%M')}"
                    for e in events) if events else ""))

        for ev in events:
            eid = ev["id"]
            if eid in self._notified:
                log.info(f"  Skipping {ev.get('summary','?')!r} — already notified")
            elif eid in self._active:
                log.info(f"  Skipping {ev.get('summary','?')!r} — popup already open")
            else:
                log.info(f"  → Showing popup: {ev.get('summary', '?')!r}")
                self._show(ev)

    def _show(self, event: dict):
        idx  = len(self._active)
        note = PostItNote(event, stack_index=idx)
        note.acknowledged.connect(self._on_ack)
        self._notified.add(event["id"])
        self._active[event["id"]] = note
        note.show()
        note.raise_()

    def _on_ack(self, event_id: str):
        self._active.pop(event_id, None)
        # Re-stack remaining notes
        for i, note in enumerate(self._active.values()):
            note.stack_index = i
            note._reposition()

    def show_test(self):
        """Show a fake post-it note so you can preview the look and feel."""
        fake_start = datetime.now(timezone.utc) + timedelta(minutes=10)
        fake_event = {
            "id": f"__test__{fake_start.timestamp()}",   # unique each time
            "summary": "Team Standup (test)",
            "start": {"dateTime": fake_start.isoformat()},
            "location": "Zoom — Conference Room B",
            "conferenceData": {
                "entryPoints": [
                    {
                        "entryPointType": "video",
                        "uri": "https://zoom.us/j/123456789",
                    }
                ]
            },
        }
        log.info("Showing test notification")
        self._show(fake_event)


# ══════════════════════════════════════════════════════════════════════════════
# Menu-bar tray icon
# ══════════════════════════════════════════════════════════════════════════════

def _make_icon() -> QIcon:
    """Draw a tiny yellow sticky-note icon for the macOS menu bar."""
    px = QPixmap(22, 22)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(YELLOW_BG)))
    p.setPen(QPen(QColor(YELLOW_STRIPE), 1.5))
    p.drawRoundedRect(1, 1, 20, 20, 3, 3)
    p.setPen(QPen(QColor(TEXT_DARK), 1.5))
    p.drawLine(5, 8, 17, 8)
    p.drawLine(5, 12, 14, 12)
    p.drawLine(5, 16, 11, 16)
    p.end()
    return QIcon(px)


def make_tray(app: QApplication, monitor: "CalendarMonitor") -> QSystemTrayIcon:
    tray = QSystemTrayIcon(_make_icon(), app)
    tray.setToolTip("Calendar Post-It — running")

    menu = QMenu()
    test_action = menu.addAction("🧪  Test notification")
    test_action.triggered.connect(monitor.show_test)
    menu.addSeparator()
    menu.addAction("Quit Calendar Post-It").triggered.connect(app.quit)

    tray.setContextMenu(menu)
    tray.show()
    return tray


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Calendar Post-It")
    app.setQuitOnLastWindowClosed(False)   # keep running after all notes dismissed

    monitor = CalendarMonitor()
    tray    = make_tray(app, monitor)      # noqa: F841  (must stay in scope)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
