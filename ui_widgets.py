"""Custom Kivy widgets for Ultimate Thief Guard.

The visual language is intentionally "security console": deep navy surfaces,
cyan idle states, crimson armed/alarm states, perforated rounded cards and a
radar-style GPS placeholder.  All widgets are pure Kivy canvas drawings, so
they scale cleanly on phones from 360 dp to tablet widths.
"""

import math

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import (
    Color,
    Ellipse,
    Line,
    Mesh,
    Rectangle,
    RoundedRectangle,
)
from kivy.metrics import dp, sp
from kivy.properties import (
    BooleanProperty,
    ListProperty,
    NumericProperty,
    StringProperty,
)
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG = [0.035, 0.055, 0.105, 1]
CARD = [0.070, 0.105, 0.175, 1]
CARD_2 = [0.100, 0.150, 0.235, 1]
TEXT = [0.935, 0.960, 1.000, 1]
MUTED = [0.560, 0.640, 0.760, 1]
CYAN = [0.130, 0.830, 0.930, 1]
GREEN = [0.130, 0.770, 0.370, 1]
RED = [0.940, 0.270, 0.270, 1]
AMBER = [0.960, 0.620, 0.040, 1]
DARK_RED = [0.260, 0.070, 0.090, 1]
BORDER = [0.160, 0.260, 0.380, 1]


def dim(color, factor):
    """Return a darker/lighter copy of an RGBA list."""
    return [max(0.0, min(1.0, value * factor)) for value in color[:3]] + [color[3]]


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------
class Card(BoxLayout):
    """Rounded panel used for every dashboard/settings section."""

    bg_color = ListProperty(CARD)
    border_color = ListProperty(BORDER)
    radius = NumericProperty(18)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(*self.bg_color)
            self._bg = RoundedRectangle(radius=[dp(self.radius)])
            self._line_color = Color(*self.border_color)
            self._line = Line(width=dp(1))
        self.bind(
            pos=self._sync,
            size=self._sync,
            bg_color=self._sync,
            border_color=self._sync,
            radius=self._sync,
        )
        self._sync()

    def _sync(self, *_args):
        self._bg_color.rgba = self.bg_color
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [dp(self.radius)]
        self._line_color.rgba = self.border_color
        self._line.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            dp(self.radius),
        )


class NeonButton(Button):
    """Flat rounded button with a neon outline and a pressed-state glow."""

    accent = ListProperty(CYAN)
    bg_color = ListProperty(CARD_2)
    border_color = ListProperty(BORDER)

    def __init__(self, **kwargs):
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_down", "")
        kwargs.setdefault("background_color", (0, 0, 0, 0))
        kwargs.setdefault("color", TEXT)
        kwargs.setdefault("font_size", sp(14))
        kwargs.setdefault("bold", True)
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(54))
        super().__init__(**kwargs)
        with self.canvas.before:
            self._fill_color = Color(*self.bg_color)
            self._fill = RoundedRectangle(radius=[dp(14)])
            self._stroke_color = Color(*self.accent)
            self._stroke = Line(width=dp(1.2))
        self.bind(
            pos=self._sync,
            size=self._sync,
            state=self._sync,
            accent=self._sync,
            bg_color=self._sync,
            border_color=self._sync,
            disabled=self._sync,
        )
        self._sync()

    def _sync(self, *_args):
        pressed = self.state == "down"
        fill = self.accent if pressed else self.bg_color
        alpha = 0.30 if pressed else 1.0
        self._fill_color.rgba = [fill[0], fill[1], fill[2], alpha]
        self._fill.pos = self.pos
        self._fill.size = self.size
        self._fill.radius = [dp(14)]
        self._stroke_color.rgba = (
            [0.35, 0.40, 0.46, 1] if self.disabled else self.accent
        )
        self._stroke.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            dp(14),
        )


class CompactButton(NeonButton):
    """Smaller action button for toolbars and the numeric keypad."""

    def __init__(self, **kwargs):
        kwargs.setdefault("height", dp(46))
        kwargs.setdefault("font_size", sp(12))
        super().__init__(**kwargs)


class StatusChip(Label):
    """Pill label such as ARMED / DISARMED / GPS LOCKED."""

    chip_color = ListProperty(CYAN)
    bg_color = ListProperty([0.0, 0.0, 0.0, 0.35])
    radius = NumericProperty(10)

    def __init__(self, **kwargs):
        kwargs.setdefault("font_size", sp(11))
        kwargs.setdefault("bold", True)
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(*self.bg_color)
            self._bg = RoundedRectangle(radius=[dp(self.radius)])
            self._line_color = Color(*self.chip_color)
            self._line = Line(width=dp(1))
        self.bind(
            pos=self._sync,
            size=self._sync,
            chip_color=self._sync,
            bg_color=self._sync,
            texture_size=self._sync,
        )
        self._sync()

    def _sync(self, *_args):
        self.texture_update()
        pad_x = dp(12)
        pad_y = dp(5)
        self.size = (
            self.texture_size[0] + pad_x * 2,
            self.texture_size[1] + pad_y * 2,
        )
        self._bg_color.rgba = self.bg_color
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [dp(self.radius)]
        self._line_color.rgba = self.chip_color
        self._line.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            dp(self.radius),
        )


# ---------------------------------------------------------------------------
# Shield
# ---------------------------------------------------------------------------
class ShieldWidget(FloatLayout):
    """Animated shield that doubles as the arm/disarm touch target."""

    armed = BooleanProperty(False)
    status = StringProperty("DISARMED")
    substatus = StringProperty("Tap shield to arm")
    glow = NumericProperty(0.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.register_event_type("on_toggle")
        self._pulse = None

        # Shield perimeter in normalized coordinates.  The first point is the
        # bottom tip; the fan triangulation below turns this outline into a
        # filled, vertically-shaded mesh.
        self._outline = [
            (0.50, 0.035),
            (0.19, 0.250),
            (0.120, 0.560),
            (0.240, 0.835),
            (0.500, 0.950),
            (0.760, 0.835),
            (0.880, 0.560),
            (0.810, 0.250),
        ]
        self._center = (0.50, 0.52)

        with self.canvas.before:
            self._glow_color = Color(*CYAN[:3], 0.15)
            self._glow_ellipse = Ellipse()
            self._mesh = Mesh(vertices=[], mode="triangles")
            self._edge_color = Color(*CYAN)
            self._edge = Line(width=dp(2.2), close=True, joint="round")
            self._inner_color = Color(1, 1, 1, 0.06)
            self._inner_edge = Line(width=dp(1), close=True)

            self._icon_color = Color(*TEXT)
            self._lock_body = RoundedRectangle(radius=[dp(3)])
            self._lock_shackle = Line(width=dp(2.6), circle=(0, 0, dp(8)))

        self.title = Label(
            text="ULTIMATE THIEF GUARD",
            font_size=sp(9),
            bold=True,
            color=MUTED,
            size_hint=(None, None),
            halign="center",
        )
        self.status_label = Label(
            text="DISARMED",
            font_size=sp(21),
            bold=True,
            color=TEXT,
            size_hint=(None, None),
            halign="center",
        )
        self.sub_label = Label(
            text="Tap shield to arm",
            font_size=sp(11),
            color=MUTED,
            size_hint=(None, None),
            halign="center",
        )
        for widget in (self.title, self.status_label, self.sub_label):
            self.add_widget(widget)
            widget.text_size = (dp(240), None)

        self.bind(
            pos=self._sync,
            size=self._sync,
            armed=self._refresh,
            status=self._refresh,
            substatus=self._refresh,
            glow=self._update_glow,
        )
        self._refresh()

    def on_toggle(self):
        pass

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.dispatch("on_toggle")
            return True
        return super().on_touch_down(touch)

    def _refresh(self, *_args):
        accent = RED if self.armed else CYAN
        self._edge_color.rgba = accent
        self._inner_color.rgba = [1, 1, 1, 0.05 if not self.armed else 0.10]
        self._glow_color.rgba = [accent[0], accent[1], accent[2], 0.15]
        self.mesh_accent = accent
        self.status_label.text = self.status
        self.status_label.color = accent if self.armed else TEXT
        self.sub_label.text = self.substatus
        self._mesh.vertices = self._build_vertices()
        self._sync()

        # Continuous "breathing" glow while armed.
        if self._pulse is not None:
            self._pulse.cancel(self)
        if self.armed:
            self._pulse = Animation(glow=0.55, duration=0.9, t="in_out_sine") + Animation(
                glow=0.14, duration=0.9, t="in_out_sine"
            )
            self._pulse.repeat = True
            self._pulse.start(self)
        else:
            self.glow = 0.0

    def _update_glow(self, *_args):
        accent = RED if self.armed else CYAN
        self._glow_color.rgba = [accent[0], accent[1], accent[2], self.glow]

    def _shade(self, y):
        """Vertical shield gradient: bright at the tip, dark near the top."""
        accent = RED if self.armed else CYAN
        dark = [0.025, 0.045, 0.085]
        blend = max(0.0, min(1.0, (y - 0.03) / 0.92))
        color = []
        for index in range(3):
            color.append(accent[index] * (1.0 - blend) + dark[index] * blend)
        return color + [0.96]

    def _build_vertices(self):
        width, height = self.width, self.height
        if width <= 1 or height <= 1:
            return []
        cx = width * self._center[0]
        cy = height * self._center[1]
        center_color = self._shade(self._center[1])
        vertices = []
        for index in range(len(self._outline)):
            x1, y1 = self._outline[index]
            x2, y2 = self._outline[(index + 1) % len(self._outline)]
            p1 = (x1 * width, y1 * height)
            p2 = (x2 * width, y2 * height)
            for px, py, color in (
                (cx, cy, center_color),
                (p1[0], p1[1], self._shade(y1)),
                (p2[0], p2[1], self._shade(y2)),
            ):
                vertices.extend([px, py, 0.0, 0.0, *color])
        return vertices

    def _sync(self, *_args):
        if self.width <= 1 or self.height <= 1:
            return
        cx = self.width * 0.5
        top_y = self.height * 0.95
        self._glow_ellipse.pos = (self.width * 0.16, self.height * 0.28)
        self._glow_ellipse.size = (self.width * 0.68, self.height * 0.62)
        self._mesh.vertices = self._build_vertices()

        points = []
        inner = []
        for x, y in self._outline:
            points.extend([x * self.width, y * self.height])
            inner.extend(
                [
                    self.width * 0.5 + (x - 0.5) * self.width * 0.86,
                    self.height * 0.5 + (y - 0.5) * self.height * 0.86,
                ]
            )
        self._edge.points = points
        self._inner_edge.points = inner

        # Lock icon
        lock_cx = self.width * 0.5
        lock_cy = self.height * 0.70
        body_w = dp(22)
        body_h = dp(17)
        self._lock_body.pos = (lock_cx - body_w / 2, lock_cy - body_h / 2)
        self._lock_body.size = (body_w, body_h)
        self._lock_shackle.circle = (
            lock_cx,
            lock_cy + body_h * 0.18,
            dp(8),
            0,
            180,
        )

        # Text positions (FloatLayout uses absolute coordinates)
        self.title.size = (dp(240), dp(18))
        self.title.text_size = (dp(240), None)
        self.title.pos = (cx - dp(120), top_y - dp(26))
        self.status_label.size = (dp(240), dp(30))
        self.status_label.text_size = (dp(240), None)
        self.status_label.pos = (cx - dp(120), self.height * 0.40)
        self.sub_label.size = (dp(240), dp(20))
        self.sub_label.text_size = (dp(240), None)
        self.sub_label.pos = (cx - dp(120), self.height * 0.28)


# ---------------------------------------------------------------------------
# Map placeholder
# ---------------------------------------------------------------------------
class MapPlaceholder(FloatLayout):
    """A live-looking map placeholder with a radar sweep and GPS marker.

    No tile server or API key is required.  When a real fix is available the
    marker changes to the live GPS coordinates; when offline the widget keeps
    the last known fix and shows the acquisition state.
    """

    has_fix = BooleanProperty(False)
    lat = NumericProperty(0.0)
    lon = NumericProperty(0.0)
    accuracy = NumericProperty(0.0)
    status = StringProperty("GPS ACQUIRING")
    _angle = NumericProperty(0.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(18)])
            self._grid_color = Color(0.13, 0.20, 0.30, 0.55)
            self._grid = Line(width=dp(0.7))
            self._road_color = Color(0.20, 0.28, 0.39, 0.75)
            self._road = Line(width=dp(3))
            self._road_2 = Line(width=dp(1.4))
            self._radar_color = Color(0.13, 0.83, 0.93, 0.20)
            self._rings = [
                Line(circle=(0, 0, 0), width=dp(1)),
                Line(circle=(0, 0, 0), width=dp(1)),
                Line(circle=(0, 0, 0), width=dp(1)),
            ]
            self._sweep_color = Color(0.13, 0.83, 0.93, 0.42)
            self._sweep = Line(width=dp(1.6))
            self._marker_glow_color = Color(0.13, 0.83, 0.93, 0.18)
            self._marker_glow = Ellipse()
            self._marker_color = Color(*CYAN)
            self._marker = Ellipse()
            self._cross_color = Color(1, 1, 1, 0.55)
            self._cross = Line(width=dp(1))

        self.live_label = Label(
            text="[b]LIVE MAP[/b]",
            markup=True,
            font_size=sp(11),
            color=TEXT,
            size_hint=(None, None),
            halign="left",
            valign="middle",
        )
        self.status_label = Label(
            text="GPS ACQUIRING",
            font_size=sp(10),
            bold=True,
            color=AMBER,
            size_hint=(None, None),
            halign="right",
            valign="middle",
        )
        self.coord_label = Label(
            text="No fix yet",
            font_size=sp(11),
            color=MUTED,
            size_hint=(None, None),
            halign="left",
        )
        for widget in (self.live_label, self.status_label, self.coord_label):
            self.add_widget(widget)

        self.bind(
            pos=self._sync,
            size=self._sync,
            has_fix=self._sync,
            status=self._sync,
            lat=self._sync,
            lon=self._sync,
        )
        Clock.schedule_interval(self._spin, 1 / 30.0)
        self._sync()

    def _spin(self, _dt):
        self._angle = (self._angle + 2.2) % 360.0
        self._sync()

    def set_location(self, lat, lon, accuracy=None):
        self.lat = float(lat)
        self.lon = float(lon)
        self.accuracy = float(accuracy or 0.0)
        self.has_fix = True
        self.status = "GPS LOCKED"
        self.coord_label.text = "%.5f, %.5f" % (self.lat, self.lon)
        if accuracy:
            self.coord_label.text += "  +/- %.0fm" % accuracy
        self._sync()

    def set_unavailable(self):
        self.has_fix = False
        self.status = "GPS ACQUIRING"
        self.coord_label.text = "No fix yet"

    def _sync(self, *_args):
        width, height = self.width, self.height
        if width <= 1 or height <= 1:
            return

        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [dp(18)]

        # Grid
        grid = []
        step = dp(30)
        x = self.x
        while x < self.x + width:
            grid.extend([x, self.y, x, self.y + height])
            x += step
        y = self.y
        while y < self.y + height:
            grid.extend([self.x, y, self.x + width, y])
            y += step
        self._grid.points = grid

        # "Roads"
        self._road.points = [
            self.x - dp(10),
            self.y + height * 0.30,
            self.x + width * 0.42,
            self.y + height * 0.52,
            self.x + width * 0.66,
            self.y + height * 0.48,
            self.x + width + dp(10),
            self.y + height * 0.72,
        ]
        self._road_2.points = [
            self.x + width * 0.22,
            self.y - dp(10),
            self.x + width * 0.30,
            self.y + height * 0.50,
            self.x + width * 0.19,
            self.y + height + dp(10),
        ]

        # Radar rings around the marker
        radius = min(width, height) * 0.33
        cx = self.x + width * 0.50
        cy = self.y + height * 0.48
        for index, ring in enumerate(self._rings):
            ring.circle = (cx, cy, radius * (index + 1) / 3.0)
        angle = math.radians(self._angle)
        self._sweep.points = [
            cx,
            cy,
            cx + radius * math.cos(angle),
            cy + radius * math.sin(angle),
        ]

        # Marker
        marker_color = CYAN if self.has_fix else AMBER
        self._marker_color.rgba = marker_color
        self._marker_glow_color.rgba = [
            marker_color[0],
            marker_color[1],
            marker_color[2],
            0.18,
        ]
        self._marker_glow.pos = (cx - dp(16), cy - dp(16))
        self._marker_glow.size = (dp(32), dp(32))
        self._marker.pos = (cx - dp(5), cy - dp(5))
        self._marker.size = (dp(10), dp(10))
        self._cross.points = [
            cx - dp(12),
            cy,
            cx + dp(12),
            cy,
            cx,
            cy - dp(12),
            cx,
            cy + dp(12),
        ]

        # Labels
        self.live_label.size = (dp(120), dp(20))
        self.live_label.text_size = self.live_label.size
        self.live_label.pos = (self.x + dp(14), self.y + height - dp(28))
        self.status_label.size = (dp(160), dp(20))
        self.status_label.text_size = self.status_label.size
        self.status_label.pos = (
            self.x + width - dp(174),
            self.y + height - dp(28),
        )
        self.status_label.text = self.status
        self.status_label.color = GREEN if self.has_fix else AMBER
        self.coord_label.size = (width - dp(28), dp(20))
        self.coord_label.text_size = self.coord_label.size
        self.coord_label.pos = (self.x + dp(14), self.y + dp(10))


# ---------------------------------------------------------------------------
# PIN pad
# ---------------------------------------------------------------------------
class PinPad(BoxLayout):
    """A four-digit on-screen keypad with dot indicators.

    Using our own keypad avoids the Android soft keyboard resizing the alarm
    screen and prevents clipboard/paste shortcuts from being used on the PIN.
    """

    length = NumericProperty(4)
    pin = StringProperty("")
    enabled_input = BooleanProperty(True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(8)
        self.size_hint_y = None
        self.height = dp(292)
        self.register_event_type("on_submit")
        self._buttons = []

        self._dots_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(34),
            spacing=dp(10),
        )
        self._dots = []
        for _ in range(int(self.length)):
            dot = Label(
                text="○",
                font_size=sp(20),
                color=MUTED,
                bold=True,
            )
            self._dots.append(dot)
            self._dots_row.add_widget(dot)
        self.add_widget(self._dots_row)

        keypad = GridLayout(
            cols=3,
            rows=4,
            spacing=dp(8),
            size_hint_y=None,
            height=dp(250),
        )
        labels = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "CLR", "0", "DEL"]
        for text in labels:
            if text in ("CLR", "DEL"):
                button = CompactButton(
                    text=text,
                    accent=AMBER if text == "CLR" else MUTED,
                    bg_color=dim(CARD, 0.9),
                    font_size=sp(12),
                )
            else:
                button = NeonButton(
                    text=text,
                    accent=CYAN,
                    bg_color=CARD_2,
                    font_size=sp(20),
                    height=dp(50),
                )
            button.bind(on_release=lambda _btn, value=text: self._press(value))
            self._buttons.append(button)
            keypad.add_widget(button)
        self.add_widget(keypad)
        self._update_dots()

    def on_submit(self, pin):
        pass

    def _press(self, value):
        if not self.enabled_input:
            return
        if value == "CLR":
            self.pin = ""
        elif value == "DEL":
            self.pin = self.pin[:-1]
        elif len(self.pin) < int(self.length):
            self.pin += value
        self._update_dots()
        if len(self.pin) == int(self.length):
            Clock.schedule_once(lambda _dt: self.dispatch("on_submit", self.pin), 0.12)

    def _update_dots(self):
        for index, dot in enumerate(self._dots):
            filled = index < len(self.pin)
            dot.text = "●" if filled else "○"
            dot.color = CYAN if filled else MUTED

    def reset(self):
        self.pin = ""
        self._update_dots()

    def set_enabled_input(self, enabled):
        self.enabled_input = bool(enabled)
        for button in self._buttons:
            button.disabled = not enabled
        if not enabled:
            self._update_dots()


# ---------------------------------------------------------------------------
# Alarm background
# ---------------------------------------------------------------------------
class PulseBackground(Widget):
    """Slowly pulsing crimson wash used behind the alarm screen."""

    intensity = NumericProperty(0.10)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._base_color = Color(0.055, 0.010, 0.018, 1)
            self._base = Rectangle()
            self._glow_color = Color(0.70, 0.05, 0.08, self.intensity)
            self._glow = Ellipse()
        self.bind(
            pos=self._sync,
            size=self._sync,
            intensity=self._sync,
        )
        self._sync()

    def _sync(self, *_args):
        self._base.pos = self.pos
        self._base.size = self.size
        self._glow_color.rgba = [0.70, 0.05, 0.08, self.intensity]
        self._glow.pos = (
            self.x - self.width * 0.15,
            self.y + self.height * 0.25,
        )
        self._glow.size = (self.width * 1.3, self.height * 0.9)


# ---------------------------------------------------------------------------
# Toast helper
# ---------------------------------------------------------------------------
_active_toast = None


class ToastLabel(Label):
    def __init__(self, **kwargs):
        kwargs.setdefault("markup", True)
        kwargs.setdefault("font_size", sp(12))
        kwargs.setdefault("color", TEXT)
        kwargs.setdefault("halign", "center")
        kwargs.setdefault("valign", "middle")
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(0.04, 0.07, 0.12, 0.96)
            self._bg = RoundedRectangle(radius=[dp(12)])
            self._border_color = Color(*CYAN)
            self._border = Line(width=dp(1))

    def set_message(self, message):
        self.text = message
        self.texture_update()
        width = min(Window.width - dp(48), self.texture_size[0] + dp(36))
        height = self.texture_size[1] + dp(20)
        self.size = (width, height)
        self.text_size = (width - dp(24), height - dp(10))
        self.pos = (
            Window.width / 2 - width / 2,
            dp(36),
        )
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [dp(12)]
        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            dp(12),
        )


def show_toast(message, duration=2.4):
    """Show a lightweight floating notification above the bottom edge."""
    global _active_toast
    if _active_toast is not None:
        try:
            Window.remove_widget(_active_toast)
        except Exception:
            pass
        _active_toast = None

    label = ToastLabel()
    label.set_message(message)
    Window.add_widget(label)
    _active_toast = label

    def _remove(_dt):
        global _active_toast
        try:
            Window.remove_widget(label)
        except Exception:
            pass
        if _active_toast is label:
            _active_toast = None

    Clock.schedule_once(_remove, duration)
    return label
