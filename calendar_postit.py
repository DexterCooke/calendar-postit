#!/usr/bin/env python3
"""
Calendar Post-It
~~~~~~~~~~~~~~~~
Polls Google Calendar every 60 seconds and pops up a yellow sticky-note
before each upcoming meeting.

Two-stage dismiss:
  1. First popup appears N minutes before the meeting (configurable).
     Clicking "Got it ✓" hides it and schedules a second reminder.
  2. Second popup appears M minutes before the meeting (configurable).
     Clicking "Dismiss" permanently closes it.
  If the second reminder is disabled in Settings, the first "Got it ✓"
  is the final dismiss.

Menu-bar icon → Settings to adjust timings live (auto-saved).
"""

import sys
import platform
import ctypes
import ctypes.util
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QSlider,
    QSystemTrayIcon, QMenu,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QFont, QColor, QPixmap, QPainter, QIcon, QBrush, QPen,
)

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR       = Path(__file__).parent.resolve()
CREDENTIALS_FILE = SCRIPT_DIR / "credentials.json"
TOKEN_FILE       = SCRIPT_DIR / "token.json"
SETTINGS_FILE    = SCRIPT_DIR / "settings.json"
SCOPES           = ["https://www.googleapis.com/auth/calendar.readonly"]

POLL_INTERVAL_MS = 60_000

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
# Settings  (persisted to settings.json, auto-saved on every change)
# ══════════════════════════════════════════════════════════════════════════════

class Settings:
    DEFAULTS = {
        "alert_minutes":            10,
        "second_reminder_enabled":  True,
        "second_reminder_minutes":  1,
    }

    def __init__(self):
        self._data = self.DEFAULTS.copy()
        self.load()

    def load(self):
        if SETTINGS_FILE.exists():
            try:
                self._data.update(json.loads(SETTINGS_FILE.read_text()))
            except Exception as exc:
                log.warning(f"Could not load settings: {exc}")

    def save(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as exc:
            log.warning(f"Could not save settings: {exc}")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def alert_minutes(self) -> int:
        return int(self._data["alert_minutes"])

    @alert_minutes.setter
    def alert_minutes(self, v: int):
        self._data["alert_minutes"] = int(v)
        self.save()

    @property
    def second_reminder_enabled(self) -> bool:
        return bool(self._data["second_reminder_enabled"])

    @second_reminder_enabled.setter
    def second_reminder_enabled(self, v: bool):
        self._data["second_reminder_enabled"] = bool(v)
        self.save()

    @property
    def second_reminder_minutes(self) -> int:
        return int(self._data["second_reminder_minutes"])

    @second_reminder_minutes.setter
    def second_reminder_minutes(self, v: int):
        self._data["second_reminder_minutes"] = int(v)
        self.save()


# ══════════════════════════════════════════════════════════════════════════════
# Google Calendar helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_calendar_service():
    """Return an authorised Google Calendar API service."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_FILE}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_upcoming_events(service, alert_minutes: int):
    """Return timed events starting within the alert window across all calendars."""
    now      = datetime.now(timezone.utc)
    time_min = now - timedelta(minutes=alert_minutes)
    time_max = now + timedelta(minutes=alert_minutes + 2)

    cal_list     = service.calendarList().list().execute()
    calendar_ids = []
    for cal in cal_list.get("items", []):
        role     = cal.get("accessRole", "")
        selected = cal.get("selected", True)
        name     = cal.get("summary", cal["id"])
        if selected and role in ("owner", "writer", "reader"):
            calendar_ids.append(cal["id"])
        log.info(f"  Calendar: {name!r}  role={role}  selected={selected}")

    events   = []
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
# Toggle switch widget
# ══════════════════════════════════════════════════════════════════════════════

class ToggleSwitch(QWidget):
    """A simple on/off toggle that matches the yellow Post-It theme."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self._checked = checked
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, v: bool):
        self._checked = v
        self.update()

    def mousePressEvent(self, _):
        self._checked = not self._checked
        self.toggled.emit(self._checked)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # Track
        track_colour = QColor(YELLOW_STRIPE) if self._checked else QColor("#cccccc")
        p.setBrush(QBrush(track_colour))
        p.drawRoundedRect(0, 3, 46, 20, 10, 10)
        # Thumb
        x = 24 if self._checked else 2
        p.setBrush(QBrush(QColor("white")))
        p.drawEllipse(x, 3, 20, 20)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
# Settings window
# ══════════════════════════════════════════════════════════════════════════════

class SettingsWindow(QWidget):
    """Floating settings panel — auto-saves every change instantly."""

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Calendar Post-It — Settings")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedWidth(340)
        self.setStyleSheet(f"background-color: {YELLOW_BG};")
        self._build_ui()
        self.adjustSize()
        self._center()

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2,
        )

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 10, 10)

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
        cl.setContentsMargins(18, 14, 18, 18)
        cl.setSpacing(10)

        # Header
        hdr_row = QHBoxLayout()
        pin = QLabel("⚙️")
        pin.setFont(QFont("Apple Color Emoji", 13))
        hdr = QLabel("Settings")
        hdr.setFont(QFont("Helvetica Neue", 14, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {TEXT_DARK};")
        hdr_row.addWidget(pin)
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        cl.addLayout(hdr_row)
        cl.addWidget(self._hairline())

        # ── First alert ──
        cl.addWidget(self._section("First Alert"))

        first_row = QHBoxLayout()
        self.first_slider = self._slider(5, 30, self.settings.alert_minutes)
        self.first_lbl    = self._value_label(f"{self.settings.alert_minutes} min before")
        self.first_slider.valueChanged.connect(self._on_first_changed)
        first_row.addWidget(self.first_slider)
        first_row.addWidget(self.first_lbl)
        cl.addLayout(first_row)
        cl.addLayout(self._range_labels("5 min", "30 min"))

        cl.addWidget(self._hairline())

        # ── Second reminder ──
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(self._section("Second Reminder"))
        toggle_row.addStretch()
        self.toggle = ToggleSwitch(checked=self.settings.second_reminder_enabled)
        self.toggle.toggled.connect(self._on_toggle_changed)
        toggle_row.addWidget(self.toggle)
        cl.addLayout(toggle_row)

        # Sub-section (shown only when toggle is on)
        self.second_widget = QWidget()
        self.second_widget.setStyleSheet(f"background: transparent;")
        sw = QVBoxLayout(self.second_widget)
        sw.setContentsMargins(0, 2, 0, 0)
        sw.setSpacing(4)

        second_row = QHBoxLayout()
        self.second_slider = self._slider(1, 10, self.settings.second_reminder_minutes)
        self.second_lbl    = self._value_label(f"{self.settings.second_reminder_minutes} min before")
        self.second_slider.valueChanged.connect(self._on_second_changed)
        second_row.addWidget(self.second_slider)
        second_row.addWidget(self.second_lbl)
        sw.addLayout(second_row)
        sw.addLayout(self._range_labels("1 min", "10 min"))

        cl.addWidget(self.second_widget)
        self.second_widget.setVisible(self.settings.second_reminder_enabled)

        cl.addWidget(self._hairline())

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFont(QFont("Helvetica Neue", 11, QFont.Weight.Medium))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(38)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {YELLOW_BTN};
                color: {TEXT_DARK};
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover   {{ background-color: {YELLOW_BTN_HOVER}; }}
            QPushButton:pressed {{ background-color: {YELLOW_BTN_DOWN};  }}
        """)
        close_btn.clicked.connect(self.close)
        cl.addWidget(close_btn)

        root.addWidget(card)

    # ── Widget helpers ────────────────────────────────────────────────────────

    def _hairline(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background-color: {YELLOW_STRIPE};")
        line.setFixedHeight(1)
        return line

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont("Helvetica Neue", 11, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {TEXT_DARK};")
        return lbl

    def _value_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont("Helvetica Neue", 11))
        lbl.setStyleSheet(f"color: {TEXT_DARK};")
        lbl.setMinimumWidth(82)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        return lbl

    def _slider(self, lo: int, hi: int, val: int) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {YELLOW_STRIPE};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {TEXT_DARK};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {TEXT_DARK};
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }}
        """)
        return s

    def _range_labels(self, lo: str, hi: str) -> QHBoxLayout:
        row = QHBoxLayout()
        for text, alignment in [(lo, Qt.AlignmentFlag.AlignLeft),
                                 (hi, Qt.AlignmentFlag.AlignRight)]:
            lbl = QLabel(text)
            lbl.setFont(QFont("Helvetica Neue", 9))
            lbl.setStyleSheet(f"color: {TEXT_MED};")
            lbl.setAlignment(alignment)
            row.addWidget(lbl)
        return row

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_first_changed(self, v: int):
        self.first_lbl.setText(f"{v} min before")
        self.settings.alert_minutes = v

    def _on_second_changed(self, v: int):
        self.second_lbl.setText(f"{v} min before")
        self.settings.second_reminder_minutes = v

    def _on_toggle_changed(self, checked: bool):
        self.settings.second_reminder_enabled = checked
        self.second_widget.setVisible(checked)
        self.adjustSize()


# ══════════════════════════════════════════════════════════════════════════════
# Post-It Note widget
# ══════════════════════════════════════════════════════════════════════════════

class PostItNote(QWidget):
    """
    Yellow sticky-note popup for one calendar event.

    is_reminder=False  →  first alert  (Got it ✓  schedules the reminder)
    is_reminder=True   →  second alert (Dismiss   = final goodbye)
    """

    acknowledged = pyqtSignal(str)   # emits event_id when button is clicked

    def __init__(self, event: dict, stack_index: int = 0, is_reminder: bool = False):
        super().__init__()
        self.event_id    = event["id"]
        self.event       = event
        self.stack_index = stack_index
        self.is_reminder = is_reminder
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

        QTimer.singleShot(150, self._force_on_top)
        self._top_timer = QTimer(self)
        self._top_timer.timeout.connect(self._force_on_top)
        self._top_timer.start(3000)

    # ── Window setup ──────────────────────────────────────────────────────────

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint  |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(340)

    # ── Always-on-top via macOS NSWindow ──────────────────────────────────────

    def _force_on_top(self):
        self.raise_()
        if platform.system() == "Darwin":
            self._set_ns_window_level(25)

    def _set_ns_window_level(self, level: int):
        try:
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
            objc.sel_registerName.restype  = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_msgSend.restype      = ctypes.c_void_p
            objc.objc_msgSend.argtypes     = [ctypes.c_void_p, ctypes.c_void_p]
            ns_view   = ctypes.c_void_p(int(self.winId()))
            ns_window = ctypes.c_void_p(
                objc.objc_msgSend(ns_view, objc.sel_registerName(b"window"))
            )
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
            objc.objc_msgSend(ns_window, objc.sel_registerName(b"setLevel:"), level)
        except Exception as exc:
            log.debug(f"NSWindow setLevel failed: {exc}")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 12, 12)

        card = QFrame()
        card.setObjectName("card")

        # Reminder gets a slightly more urgent stripe
        stripe = "#E05000" if self.is_reminder else YELLOW_STRIPE
        card.setStyleSheet(f"""
            QFrame#card {{
                background-color: {YELLOW_BG};
                border-radius: 4px;
                border-top: 5px solid {stripe};
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

        # Header
        hrow = QHBoxLayout()
        icon = "⚠️" if self.is_reminder else "📌"
        text = "Starting Soon!" if self.is_reminder else "Upcoming Meeting"
        pin  = QLabel(icon)
        pin.setFont(QFont("Apple Color Emoji", 13))
        hdr = QLabel(text)
        hdr.setFont(QFont("Helvetica Neue", 10, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {'#c05000' if self.is_reminder else TEXT_MED};")
        hrow.addWidget(pin)
        hrow.addWidget(hdr)
        hrow.addStretch()
        cl.addLayout(hrow)

        # Hairline
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background-color: {stripe};")
        line.setFixedHeight(1)
        cl.addWidget(line)

        # Title
        title = QLabel(self.event.get("summary", "Untitled Meeting"))
        title.setFont(QFont("Helvetica Neue", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TEXT_DARK};")
        title.setWordWrap(True)
        cl.addWidget(title)

        # Time
        local_start = self.start_time.astimezone()
        t_lbl = QLabel(f"🕐  {local_start.strftime('%-I:%M %p')}")
        t_lbl.setFont(QFont("Helvetica Neue", 11))
        t_lbl.setStyleSheet(f"color: {TEXT_MED};")
        cl.addWidget(t_lbl)

        # Location
        location = self.event.get("location", "").strip()
        if location:
            loc = QLabel(f"📍  {location}")
            loc.setFont(QFont("Helvetica Neue", 10))
            loc.setStyleSheet(f"color: {TEXT_MED};")
            loc.setWordWrap(True)
            cl.addWidget(loc)

        # Video link
        video_uri = self._get_video_uri()
        if video_uri:
            vid = QLabel(
                f'🎥  <a href="{video_uri}" style="color:{TEXT_MED};">Join video call</a>'
            )
            vid.setFont(QFont("Helvetica Neue", 10))
            vid.setOpenExternalLinks(True)
            cl.addWidget(vid)

        # Countdown
        self.countdown_lbl = QLabel("")
        self.countdown_lbl.setFont(QFont("Helvetica Neue", 28, QFont.Weight.Bold))
        self.countdown_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_lbl.setStyleSheet(f"color: {TEXT_DARK}; margin: 6px 0 4px 0;")
        cl.addWidget(self.countdown_lbl)

        # Button — label differs by stage
        btn_text = "Dismiss  ✓" if self.is_reminder else "Got it  ✓"
        btn = QPushButton(btn_text)
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
        for ep in self.event.get("conferenceData", {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                return ep.get("uri")
        return None

    def _reposition(self):
        screen = QApplication.primaryScreen().availableGeometry()
        h = max(self.sizeHint().height(), 250)
        x = screen.right() - self.width() - 24
        y = screen.bottom() - h - 24 - self.stack_index * (h + 14)
        self.move(x, max(screen.top() + 10, y))

    def _update_countdown(self):
        secs = int((self.start_time - datetime.now(timezone.utc)).total_seconds())
        if secs <= 0:
            self.countdown_lbl.setText("🔔 Starting now!")
            self.countdown_lbl.setStyleSheet(
                "color: #cc0000; font-size: 18px; margin: 6px 0 4px 0;"
            )
        elif secs < 60:
            self.countdown_lbl.setText(f"⏱  {secs}s")
            self.countdown_lbl.setStyleSheet("color: #c05000; margin: 6px 0 4px 0;")
        else:
            m, s = divmod(secs, 60)
            self.countdown_lbl.setText(f"⏱  {m}m {s:02d}s")
            self.countdown_lbl.setStyleSheet(
                f"color: {TEXT_DARK}; margin: 6px 0 4px 0;"
            )

    def _acknowledge(self):
        self._tick.stop()
        self._top_timer.stop()
        self.acknowledged.emit(self.event_id)
        self.close()

    # ── Drag ──────────────────────────────────────────────────────────────────

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if ev.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _):
        self._drag_pos = None


# ══════════════════════════════════════════════════════════════════════════════
# Calendar Monitor
# ══════════════════════════════════════════════════════════════════════════════

class CalendarMonitor:
    """Polls Google Calendar and manages the two-stage popup lifecycle."""

    def __init__(self, settings: Settings):
        self.settings = settings

        self._notified:        set[str]             = set()   # had first popup
        self._fully_dismissed: set[str]             = set()   # no more popups ever
        self._active:          dict[str, PostItNote] = {}
        self._reminder_timers: dict[str, QTimer]    = {}

        try:
            get_calendar_service()
            log.info("✓ Google Calendar connected")
        except Exception as exc:
            log.error(f"Calendar auth failed on startup: {exc}")

        self._poll = QTimer()
        self._poll.timeout.connect(self._check)
        self._poll.start(POLL_INTERVAL_MS)
        QTimer.singleShot(800, self._check)

    # ── Poll ──────────────────────────────────────────────────────────────────

    def _check(self):
        now = datetime.now(timezone.utc)
        log.info(
            f"Poll at {now.astimezone().strftime('%H:%M:%S')} — window: "
            f"{(now - timedelta(minutes=self.settings.alert_minutes)).astimezone().strftime('%H:%M')}"
            f" → "
            f"{(now + timedelta(minutes=self.settings.alert_minutes + 2)).astimezone().strftime('%H:%M')}"
        )

        try:
            service = get_calendar_service()
        except Exception as exc:
            log.error(f"Auth failed: {exc}")
            return

        try:
            events = fetch_upcoming_events(service, self.settings.alert_minutes)
        except Exception as exc:
            log.error(f"Fetch failed: {exc}")
            return

        log.info(
            f"  Found {len(events)} event(s)"
            + (": " + ", ".join(
                f"{e.get('summary','?')!r} @ "
                f"{datetime.fromisoformat(e['start']['dateTime']).astimezone().strftime('%H:%M')}"
                for e in events
            ) if events else "")
        )

        for ev in events:
            eid = ev["id"]
            if eid in self._fully_dismissed:
                pass
            elif eid in self._notified:
                log.info(f"  Skipping {ev.get('summary','?')!r} — already notified")
            elif eid in self._active:
                log.info(f"  Skipping {ev.get('summary','?')!r} — popup open")
            else:
                self._show(ev, is_reminder=False)

    # ── Show / dismiss ────────────────────────────────────────────────────────

    def _show(self, event: dict, is_reminder: bool):
        idx  = len(self._active)
        note = PostItNote(event, stack_index=idx, is_reminder=is_reminder)

        if is_reminder:
            note.acknowledged.connect(self._on_reminder_ack)
        else:
            note.acknowledged.connect(self._on_first_ack)

        self._notified.add(event["id"])
        self._active[event["id"]] = note
        note.show()
        note.raise_()

    def _on_first_ack(self, event_id: str):
        """First popup was dismissed — schedule reminder if enabled."""
        note = self._active.pop(event_id, None)
        self._restack()

        if note and self.settings.second_reminder_enabled:
            reminder_at = note.start_time - timedelta(
                minutes=self.settings.second_reminder_minutes
            )
            delay_ms = int(
                (reminder_at - datetime.now(timezone.utc)).total_seconds() * 1000
            )

            if delay_ms > 500:
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(lambda e=note.event: self._fire_reminder(e))
                timer.start(delay_ms)
                self._reminder_timers[event_id] = timer
                log.info(
                    f"Reminder scheduled for {note.event.get('summary','?')!r} "
                    f"in {delay_ms / 1000:.0f}s"
                )
            else:
                # Already at or past reminder time — show immediately
                self._fire_reminder(note.event)
        else:
            # Second reminder disabled — first ack is final
            self._fully_dismissed.add(event_id)

    def _fire_reminder(self, event: dict):
        eid = event["id"]
        self._reminder_timers.pop(eid, None)
        if eid not in self._fully_dismissed:
            log.info(f"Showing reminder for {event.get('summary','?')!r}")
            self._show(event, is_reminder=True)

    def _on_reminder_ack(self, event_id: str):
        """Second popup dismissed — permanently done."""
        self._active.pop(event_id, None)
        self._fully_dismissed.add(event_id)
        # Cancel any stale timer just in case
        if timer := self._reminder_timers.pop(event_id, None):
            timer.stop()
        self._restack()

    def _restack(self):
        for i, note in enumerate(self._active.values()):
            note.stack_index = i
            note._reposition()

    # ── Test ──────────────────────────────────────────────────────────────────

    def show_test(self):
        """Show a fake first-alert popup (goes through the full two-stage flow)."""
        fake_start = datetime.now(timezone.utc) + timedelta(
            minutes=self.settings.alert_minutes
        )
        fake_event = {
            "id": f"__test__{fake_start.timestamp()}",
            "summary": "Team Standup (test)",
            "start": {"dateTime": fake_start.isoformat()},
            "location": "Zoom — Conference Room B",
            "conferenceData": {
                "entryPoints": [{
                    "entryPointType": "video",
                    "uri": "https://zoom.us/j/123456789",
                }]
            },
        }
        log.info("Showing test notification")
        self._show(fake_event, is_reminder=False)


# ══════════════════════════════════════════════════════════════════════════════
# Menu-bar tray icon
# ══════════════════════════════════════════════════════════════════════════════

def _make_icon() -> QIcon:
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


def make_tray(app: QApplication, monitor: CalendarMonitor, settings: Settings) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(_make_icon(), app)
    tray.setToolTip("Calendar Post-It — running")

    # Keep settings window as singleton
    _settings_win: list[SettingsWindow | None] = [None]

    def open_settings():
        if _settings_win[0] is None or not _settings_win[0].isVisible():
            _settings_win[0] = SettingsWindow(settings)
        _settings_win[0].show()
        _settings_win[0].raise_()
        _settings_win[0].activateWindow()

    menu = QMenu()
    menu.addAction("⚙️  Settings").triggered.connect(open_settings)
    menu.addAction("🧪  Test notification").triggered.connect(monitor.show_test)
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
    app.setQuitOnLastWindowClosed(False)

    settings = Settings()
    monitor  = CalendarMonitor(settings)
    tray     = make_tray(app, monitor, settings)  # noqa: F841

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
