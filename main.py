"""Ultimate Thief Guard - Kivy application entry point.

Run on a desktop for UI development (Android APIs are stubbed), or package it
with the included ``buildozer.spec`` for Android.

Module map
----------
main.py          Screens, navigation, alarm workflow, evidence collection
android_bridge   AudioManager / Telephony / Location / Camera2 / power hooks
guard_core       PIN storage, motion detection, alarm WAV generation
ui_widgets       Shield, map placeholder, PIN pad, cards, toasts

Security model
--------------
* The owner PIN is stored as a PBKDF2-HMAC-SHA256 digest, never plain text.
* Wrong PINs trigger an escalating lockout while the siren keeps running.
* The alarm is stopped only by a verified PIN, never by a UI button.
* All Android API use is best-effort and wrapped so one failed subsystem
  cannot silence the alarm.
"""

import os
import re
import threading
from datetime import datetime

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import FadeTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.utils import platform

from android_bridge import AndroidBridge, PowerShield, android_available
from guard_core import MotionGuard, SecurityStore, ensure_alarm_wav
from ui_widgets import (
    AMBER,
    BG,
    BORDER,
    CARD_2,
    CYAN,
    GREEN,
    MUTED,
    RED,
    TEXT,
    Card,
    CompactButton,
    MapPlaceholder,
    NeonButton,
    PinPad,
    PulseBackground,
    ShieldWidget,
    StatusChip,
    show_toast,
)

APP_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Small UI helpers
# ---------------------------------------------------------------------------


def make_label(
    text,
    size=12,
    color=TEXT,
    bold=False,
    height=None,
    halign="left",
    valign="middle",
    **kwargs
):
    label = Label(
        text=text,
        font_size=sp(size),
        color=color,
        bold=bold,
        halign=halign,
        valign=valign,
        **kwargs
    )
    label.bind(
        width=lambda instance, value: setattr(
            instance, "text_size", (max(0, value), None)
        )
    )
    if height is not None:
        label.size_hint_y = None
        label.height = dp(height)
    return label


def auto_label(text, size=12, color=MUTED, bold=False, **kwargs):
    """Label that grows vertically to fit wrapped text."""
    label = make_label(text, size=size, color=color, bold=bold, **kwargs)
    label.size_hint_y = None
    label.height = dp(20)
    label.valign = "top"
    label.bind(
        texture_size=lambda instance, value: setattr(
            instance, "height", max(dp(20), value[1] + dp(2))
        )
    )
    return label


def section_label(text):
    return make_label(text, size=10, color=CYAN, bold=True, height=18)


def normalize_phone(number):
    return re.sub(r"[^0-9+]", "", str(number or ""))


def valid_phone(number):
    cleaned = normalize_phone(number)
    if not re.fullmatch(r"\+?[0-9]{7,18}", cleaned):
        return False
    return True


def masked_number(number):
    cleaned = normalize_phone(number)
    if not cleaned:
        return "NOT SET"
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return "*" * (len(cleaned) - 4) + cleaned[-4:]


def short_time(value):
    if not value:
        return "Never"
    try:
        return str(value).replace("T", " ")[:19]
    except Exception:
        return str(value)


class ConfirmPopup(Popup):
    """Dark confirmation popup used before destructive/test actions."""

    def __init__(self, title, message, on_confirm, confirm_text="CONFIRM", danger=False, **kwargs):
        kwargs.setdefault("size_hint", (0.90, None))
        kwargs.setdefault("height", dp(280))
        kwargs.setdefault("auto_dismiss", False)
        kwargs.setdefault("title", title)
        kwargs.setdefault("title_color", TEXT)
        kwargs.setdefault("separator_color", BORDER)
        kwargs.setdefault("background_color", BG)
        super().__init__(**kwargs)
        self._on_confirm = on_confirm

        content = BoxLayout(
            orientation="vertical",
            padding=dp(16),
            spacing=dp(12),
        )
        content.add_widget(
            auto_label(message, size=12, color=MUTED, halign="left")
        )
        spacer = Label(size_hint_y=1)
        content.add_widget(spacer)
        row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(46),
            spacing=dp(8),
        )
        cancel = CompactButton(
            text="CANCEL",
            accent=MUTED,
            size_hint_x=1,
            height=dp(46),
        )
        confirm = CompactButton(
            text=confirm_text,
            accent=RED if danger else GREEN,
            size_hint_x=1,
            height=dp(46),
        )
        cancel.bind(on_release=lambda _btn: self.dismiss())
        confirm.bind(on_release=self._confirm)
        row.add_widget(cancel)
        row.add_widget(confirm)
        content.add_widget(row)
        self.content = content

    def _confirm(self, _button):
        self.dismiss()
        try:
            self._on_confirm()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# First-run setup
# ---------------------------------------------------------------------------
class SetupScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.step = 0
        self.first_pin = ""

        root = FloatLayout()
        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(20), dp(18), dp(20), dp(26)],
            spacing=dp(10),
        )
        content.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        self.content = content
        scroll.add_widget(content)
        root.add_widget(scroll)

        content.add_widget(
            make_label(
                "ULTIMATE THIEF GUARD",
                size=23,
                color=TEXT,
                bold=True,
                height=34,
            )
        )
        content.add_widget(
            make_label(
                "INITIALIZE SECURITY CORE",
                size=10,
                color=CYAN,
                bold=True,
                height=18,
            )
        )
        self.step_label = make_label(
            "STEP 1 OF 3",
            size=10,
            color=AMBER,
            bold=True,
            height=20,
        )
        content.add_widget(self.step_label)
        self.prompt_label = auto_label(
            "Create a 4-digit owner PIN",
            size=17,
            color=TEXT,
            bold=True,
        )
        content.add_widget(self.prompt_label)
        self.help_label = auto_label(
            "The PIN is required to disarm the alarm and unlock the guard.",
            size=11,
            color=MUTED,
        )
        content.add_widget(self.help_label)

        self.pin_box = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(6),
        )
        self.pin_box.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        self.pin_pad = PinPad()
        self.pin_pad.bind(on_submit=self._on_pin_submitted)
        self.pin_box.add_widget(self.pin_pad)
        content.add_widget(self.pin_box)

        self.emergency_box = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(8),
        )
        self.emergency_box.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        self.emergency_input = TextInput(
            hint_text="+1 555 010 2030",
            multiline=False,
            input_type="number",
            font_size=sp(18),
            size_hint_y=None,
            height=dp(52),
            background_color=CARD_2,
            foreground_color=TEXT,
            cursor_color=CYAN,
            hint_text_color=MUTED,
            padding=[dp(14), dp(14), dp(14), dp(14)],
        )
        self.emergency_box.add_widget(self.emergency_input)
        self.create_button = NeonButton(
            text="CREATE SECURITY PROFILE",
            accent=GREEN,
            height=dp(54),
        )
        self.create_button.bind(on_release=self._finish_setup)
        self.emergency_box.add_widget(self.create_button)

        self.error_label = auto_label("", size=11, color=RED)
        content.add_widget(self.error_label)
        content.add_widget(
            auto_label(
                "Permissions for camera, SMS and location will be requested "
                "after setup. Android will display its own system dialogs.",
                size=10,
                color=MUTED,
            )
        )

        self.add_widget(root)
        self._refresh_step()

    # -- PIN flow ----------------------------------------------------------
    def _on_pin_submitted(self, _pin_pad, pin):
        self.error_label.text = ""
        if self.step == 0:
            if len(pin) != 4:
                self._error("PIN must be exactly 4 digits.")
                return
            self.first_pin = pin
            self.step = 1
            self._refresh_step()
            return

        if self.step == 1:
            if pin != self.first_pin:
                self.first_pin = ""
                self.step = 0
                self._refresh_step()
                self._error("PINs did not match. Start again.")
                return
            self.step = 2
            self._refresh_step()
            return

    def _refresh_step(self):
        self.pin_pad.reset()
        self.pin_pad.set_enabled_input(True)
        self.error_label.text = ""

        if self.step == 2:
            self.step_label.text = "STEP 3 OF 3  •  EMERGENCY CONTACT"
            self.prompt_label.text = "Emergency mobile number"
            self.help_label.text = (
                "The live GPS link and SOS alert will be sent to this number."
            )
            if self.pin_box.parent is not None:
                self.content.remove_widget(self.pin_box)
            if self.emergency_box.parent is None:
                self.content.add_widget(self.emergency_box)
            Clock.schedule_once(lambda _dt: setattr(self.emergency_input, "focus", True), 0.1)
        else:
            self.emergency_input.focus = False
            self.step_label.text = "STEP %d OF 3  •  OWNER PIN" % (self.step + 1)
            if self.step == 0:
                self.prompt_label.text = "Create a 4-digit owner PIN"
                self.help_label.text = (
                    "This secret PIN is the only way to stop an active alarm."
                )
            else:
                self.prompt_label.text = "Confirm your 4-digit PIN"
                self.help_label.text = "Re-enter the same PIN to continue."
            if self.emergency_box.parent is not None:
                self.content.remove_widget(self.emergency_box)
            if self.pin_box.parent is None:
                self.content.add_widget(self.pin_box)

    def _error(self, message):
        self.error_label.text = message

    def _finish_setup(self, _button):
        number = self.emergency_input.text.strip()
        if not valid_phone(number):
            self._error(
                "Enter a valid emergency number (7-18 digits, optional +)."
            )
            return
        if len(self.first_pin) != 4:
            self._error("PIN setup was not completed.")
            return
        self.app.complete_setup(self.first_pin, normalize_phone(number))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class DashboardScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app

        root = BoxLayout()
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(2))
        content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(16), dp(10), dp(16), dp(18)],
            spacing=dp(10),
        )
        content.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        scroll.add_widget(content)
        root.add_widget(scroll)

        # Top bar ---------------------------------------------------------
        header = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(46),
            spacing=dp(8),
        )
        title_box = BoxLayout(orientation="vertical")
        title_box.add_widget(
            make_label("THIEF GUARD", size=17, color=TEXT, bold=True, height=24)
        )
        title_box.add_widget(
            make_label(
                "SECURITY CONSOLE",
                size=8,
                color=MUTED,
                bold=True,
                height=14,
            )
        )
        header.add_widget(title_box)

        self.status_chip = StatusChip(text="DISARMED", chip_color=CYAN)
        header.add_widget(self.status_chip)

        self.settings_button = CompactButton(
            text="SETTINGS",
            accent=MUTED,
            size_hint=(None, None),
            width=dp(92),
            height=dp(38),
        )
        self.settings_button.bind(on_release=lambda _btn: self.app.open_settings())
        header.add_widget(self.settings_button)
        content.add_widget(header)

        # Shield / main toggle -------------------------------------------
        self.shield = ShieldWidget(size_hint_y=None, height=dp(238))
        self.shield.bind(on_toggle=lambda _w: self.app.toggle_arm())
        content.add_widget(self.shield)

        self.toggle_button = NeonButton(
            text="ARM SYSTEM",
            accent=CYAN,
            height=dp(56),
        )
        self.toggle_button.bind(on_release=lambda _btn: self.app.toggle_arm())
        content.add_widget(self.toggle_button)

        # Quick actions ---------------------------------------------------
        quick = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(46),
            spacing=dp(8),
        )
        self.find_button = CompactButton(
            text="FIND DEVICE",
            accent=CYAN,
            height=dp(46),
        )
        self.find_button.bind(on_release=lambda _btn: self.app.find_device())
        self.test_button = CompactButton(
            text="TEST ALARM",
            accent=AMBER,
            height=dp(46),
        )
        self.test_button.bind(
            on_release=lambda _btn: self.app.confirm_test_alarm()
        )
        quick.add_widget(self.find_button)
        quick.add_widget(self.test_button)
        content.add_widget(quick)

        # Map placeholder -------------------------------------------------
        self.map = MapPlaceholder(size_hint_y=None, height=dp(188))
        content.add_widget(self.map)

        # Status card -----------------------------------------------------
        info = Card(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(14), dp(10), dp(14), dp(10)],
            spacing=dp(3),
        )
        info.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        self.contact_label = make_label("", size=11, color=MUTED, height=18)
        self.sensor_label = make_label("", size=11, color=MUTED, height=18)
        self.sensitivity_label = make_label(
            "", size=11, color=MUTED, height=18
        )
        info.add_widget(self.contact_label)
        info.add_widget(self.sensor_label)
        info.add_widget(self.sensitivity_label)
        content.add_widget(info)

        content.add_widget(
            auto_label(
                "Keep the app open while armed. Android may suspend normal "
                "background apps; a foreground service build is described in "
                "the project comments.",
                size=9,
                color=MUTED,
                halign="center",
            )
        )

        self.add_widget(root)
        Clock.schedule_once(lambda _dt: self.refresh(), 0)

    def on_enter(self):
        self.refresh()

    def refresh(self):
        store = self.app.store
        armed = bool(store.armed)
        self.shield.armed = armed
        self.shield.status = "ARMED" if armed else "DISARMED"
        self.shield.substatus = (
            "Motion guard active" if armed else "Tap shield to arm"
        )
        self.status_chip.text = "ARMED" if armed else "DISARMED"
        self.status_chip.chip_color = RED if armed else CYAN
        self.status_chip.bg_color = (
            [0.30, 0.05, 0.08, 0.55] if armed else [0.04, 0.20, 0.26, 0.55]
        )
        self.toggle_button.text = (
            "DISARM SYSTEM" if armed else "ARM SYSTEM"
        )
        self.toggle_button.accent = RED if armed else CYAN

        location = store.last_location()
        if location is not None:
            self.map.set_location(location[0], location[1])
        else:
            self.map.set_unavailable()

        emergency = store.emergency
        self.contact_label.text = "EMERGENCY CONTACT   %s" % masked_number(
            emergency
        )
        sensor_state = self.app.motion.status
        self.sensor_label.text = "MOTION SENSOR   %s" % sensor_state
        self.sensitivity_label.text = (
            "SENSITIVITY   %d / 10" % store.sensitivity
        )

    def update_location(self, location):
        if location:
            self.map.set_location(
                location["lat"],
                location["lon"],
                location.get("accuracy"),
            )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class SettingsScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._loading = False

        root = BoxLayout(orientation="vertical")
        header = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(52),
            padding=[dp(16), dp(8), dp(16), dp(0)],
            spacing=dp(8),
        )
        back = CompactButton(
            text="BACK",
            accent=CYAN,
            size_hint=(None, None),
            width=dp(82),
            height=dp(40),
        )
        back.bind(on_release=lambda _btn: self.app.go_dashboard())
        header.add_widget(back)
        header.add_widget(
            make_label("SETTINGS", size=16, color=TEXT, bold=True, height=40)
        )
        root.add_widget(header)

        scroll = ScrollView(do_scroll_x=False, bar_width=dp(2))
        content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(16), dp(6), dp(16), dp(24)],
            spacing=dp(12),
        )
        content.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        scroll.add_widget(content)
        root.add_widget(scroll)

        # Emergency contact card -----------------------------------------
        emergency_card = Card(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(16), dp(12), dp(16), dp(12)],
            spacing=dp(7),
        )
        emergency_card.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        emergency_card.add_widget(section_label("EMERGENCY CONTACT"))
        emergency_card.add_widget(
            auto_label(
                "SOS SMS and the live Google Maps link are sent to this number.",
                size=10,
                color=MUTED,
            )
        )
        self.emergency_input = TextInput(
            hint_text="+1 555 010 2030",
            multiline=False,
            input_type="number",
            font_size=sp(17),
            size_hint_y=None,
            height=dp(50),
            background_color=CARD_2,
            foreground_color=TEXT,
            cursor_color=CYAN,
            hint_text_color=MUTED,
            padding=[dp(14), dp(13), dp(14), dp(13)],
        )
        emergency_card.add_widget(self.emergency_input)
        save_number = CompactButton(
            text="SAVE NUMBER",
            accent=GREEN,
            height=dp(46),
        )
        save_number.bind(
            on_release=lambda _btn: self.app.save_emergency(
                self.emergency_input.text
            )
        )
        emergency_card.add_widget(save_number)
        content.add_widget(emergency_card)

        # Sensitivity card ------------------------------------------------
        sensitivity_card = Card(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(16), dp(12), dp(16), dp(12)],
            spacing=dp(5),
        )
        sensitivity_card.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        sensitivity_card.add_widget(section_label("MOTION SENSITIVITY"))
        self.sensitivity_label = make_label(
            "", size=11, color=TEXT, height=18
        )
        sensitivity_card.add_widget(self.sensitivity_label)
        self.slider = Slider(
            min=1,
            max=10,
            step=1,
            value=self.app.store.sensitivity,
            size_hint_y=None,
            height=dp(42),
            cursor_size=(dp(22), dp(22)),
        )
        self.slider.bind(value=self._on_sensitivity_changed)
        sensitivity_card.add_widget(self.slider)
        sensitivity_card.add_widget(
            auto_label(
                "Higher values react to smaller movements and may increase "
                "false alarms. Lower values are better for windy vehicles.",
                size=10,
                color=MUTED,
            )
        )
        content.add_widget(sensitivity_card)

        # Security actions -------------------------------------------------
        actions_card = Card(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(16), dp(12), dp(16), dp(12)],
            spacing=dp(7),
        )
        actions_card.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        actions_card.add_widget(section_label("SECURITY ACTIONS"))

        change_pin = CompactButton(
            text="CHANGE OWNER PIN",
            accent=CYAN,
            height=dp(46),
        )
        change_pin.bind(on_release=lambda _btn: self.app.show_change_pin())
        actions_card.add_widget(change_pin)

        test_alarm = CompactButton(
            text="TEST ALARM  (NO SOS)",
            accent=AMBER,
            height=dp(46),
        )
        test_alarm.bind(
            on_release=lambda _btn: self.app.confirm_test_alarm()
        )
        actions_card.add_widget(test_alarm)

        test_sms = CompactButton(
            text="SEND TEST SOS SMS",
            accent=AMBER,
            height=dp(46),
        )
        test_sms.bind(
            on_release=lambda _btn: self.app.confirm_test_sms()
        )
        actions_card.add_widget(test_sms)

        permissions = CompactButton(
            text="REQUEST ANDROID PERMISSIONS",
            accent=GREEN,
            height=dp(46),
        )
        permissions.bind(
            on_release=lambda _btn: self.app.request_permissions()
        )
        actions_card.add_widget(permissions)

        overlay = CompactButton(
            text="OVERLAY PERMISSION  (POWER SHIELD)",
            accent=MUTED,
            height=dp(46),
        )
        overlay.bind(
            on_release=lambda _btn: self.app.bridge.request_overlay_permission()
        )
        actions_card.add_widget(overlay)
        content.add_widget(actions_card)

        # Diagnostics ------------------------------------------------------
        diagnostics = Card(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(16), dp(12), dp(16), dp(12)],
            spacing=dp(4),
        )
        diagnostics.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        diagnostics.add_widget(section_label("SYSTEM DIAGNOSTICS"))
        self.permission_label = auto_label("", size=10, color=MUTED)
        self.evidence_label = auto_label("", size=10, color=MUTED)
        self.alarm_label = auto_label("", size=10, color=MUTED)
        self.version_label = auto_label(
            "Build %s  •  %s" % (APP_VERSION, platform.upper()),
            size=10,
            color=MUTED,
        )
        for widget in (
            self.permission_label,
            self.evidence_label,
            self.alarm_label,
            self.version_label,
        ):
            diagnostics.add_widget(widget)
        content.add_widget(diagnostics)

        # Limitations ------------------------------------------------------
        about = Card(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(16), dp(12), dp(16), dp(12)],
            spacing=dp(4),
        )
        about.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        about.add_widget(section_label("IMPORTANT ANDROID NOTES"))
        about.add_widget(
            auto_label(
                "• Silent-mode override uses AudioManager + USAGE_ALARM and "
                "works on normal devices; Do Not Disturb policy can still "
                "restrict ringer changes.\n"
                "• Camera capture and GPS are blocked by Android if the "
                "screen is off or permissions are denied.\n"
                "• A normal app cannot completely block the hardware power "
                "button. The guard keeps the screen awake, re-shows the PIN "
                "overlay, and sends a final SOS on shutdown. True blocking "
                "requires device-owner/lock-task provisioning.\n"
                "• For guarding while the app is backgrounded, enable the "
                "optional foreground service noted in the project files.",
                size=10,
                color=MUTED,
            )
        )
        content.add_widget(about)

        self.add_widget(root)
        Clock.schedule_once(lambda _dt: self.refresh(), 0)

    def on_enter(self):
        self.refresh()

    def refresh(self):
        self._loading = True
        try:
            self.emergency_input.text = self.app.store.emergency
            self.slider.value = self.app.store.sensitivity
            self._update_sensitivity_label(int(self.slider.value))
            self.permission_label.text = (
                "PERMISSIONS   " + self.app.bridge.permission_report()
            )
            last_capture = self.app.store.last_capture
            self.evidence_label.text = (
                "LAST INTRUDER PHOTO   %s"
                % (last_capture if last_capture else "None")
            )
            self.alarm_label.text = (
                "LAST ALARM   %s"
                % short_time(self.app.store._read().get("last_alarm", ""))
            )
        finally:
            self._loading = False

    def _on_sensitivity_changed(self, _slider, value):
        self._update_sensitivity_label(int(value))
        if not self._loading:
            self.app.set_sensitivity(int(value))

    def _update_sensitivity_label(self, value):
        descriptions = {
            1: "Very low",
            2: "Low",
            3: "Low",
            4: "Medium-low",
            5: "Balanced",
            6: "Medium-high",
            7: "High",
            8: "High",
            9: "Very high",
            10: "Maximum",
        }
        self.sensitivity_label.text = "LEVEL %d / 10  •  %s" % (
            value,
            descriptions.get(value, "Custom"),
        )


# ---------------------------------------------------------------------------
# Alarm overlay
# ---------------------------------------------------------------------------
class AlarmScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._pulse_animation = None
        self._countdown_event = None

        root = FloatLayout()
        self.pulse = PulseBackground(size_hint=(1, 1))
        root.add_widget(self.pulse)

        scroll = ScrollView(
            do_scroll_x=False,
            size_hint=(1, 1),
            bar_width=dp(2),
        )
        content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(20), dp(22), dp(20), dp(22)],
            spacing=dp(9),
        )
        content.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        scroll.add_widget(content)
        root.add_widget(scroll)

        row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(24),
        )
        row.add_widget(Label())
        self.alarm_chip = StatusChip(
            text="ALARM ACTIVE",
            chip_color=RED,
            bg_color=[0.35, 0.03, 0.06, 0.7],
        )
        row.add_widget(self.alarm_chip)
        row.add_widget(Label())
        content.add_widget(row)

        content.add_widget(
            make_label(
                "SYSTEM BREACH",
                size=27,
                color=RED,
                bold=True,
                height=40,
                halign="center",
            )
        )
        content.add_widget(
            make_label(
                "UNAUTHORIZED MOVEMENT DETECTED",
                size=10,
                color=MUTED,
                bold=True,
                height=18,
                halign="center",
            )
        )
        self.reason_label = auto_label(
            "", size=12, color=TEXT, halign="center"
        )
        content.add_widget(self.reason_label)
        self.test_label = auto_label(
            "", size=10, color=AMBER, halign="center", bold=True
        )
        content.add_widget(self.test_label)

        content.add_widget(
            make_label(
                "ENTER OWNER PIN TO DISARM",
                size=11,
                color=TEXT,
                bold=True,
                height=22,
                halign="center",
            )
        )
        self.pin_pad = PinPad()
        self.pin_pad.bind(on_submit=self._on_pin_submitted)
        content.add_widget(self.pin_pad)
        self.attempts_label = auto_label(
            "", size=11, color=RED, halign="center"
        )
        self.lockout_label = auto_label(
            "", size=11, color=AMBER, halign="center"
        )
        content.add_widget(self.attempts_label)
        content.add_widget(self.lockout_label)

        intel = Card(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(14), dp(10), dp(14), dp(10)],
            spacing=dp(3),
        )
        intel.bind(
            minimum_height=lambda instance, value: setattr(
                instance, "height", value
            )
        )
        intel.add_widget(section_label("THREAT RESPONSE"))
        self.camera_label = auto_label("", size=10, color=MUTED)
        self.gps_label = auto_label("", size=10, color=MUTED)
        self.sos_label = auto_label("", size=10, color=MUTED)
        intel.add_widget(self.camera_label)
        intel.add_widget(self.gps_label)
        intel.add_widget(self.sos_label)
        self.photo = Image(
            size_hint_y=None,
            height=0,
            opacity=0,
            allow_stretch=True,
            keep_ratio=True,
        )
        intel.add_widget(self.photo)
        content.add_widget(intel)

        content.add_widget(
            auto_label(
                "The siren continues until the correct owner PIN is entered. "
                "Emergency services are not contacted automatically.",
                size=9,
                color=MUTED,
                halign="center",
            )
        )

        self.add_widget(root)

    def on_enter(self):
        self._start_pulse()

    def on_leave(self):
        self._stop_pulse()
        self._cancel_countdown()

    def _start_pulse(self):
        if self._pulse_animation is not None:
            return
        self._pulse_animation = Animation(
            intensity=0.32, duration=0.85, t="in_out_sine"
        ) + Animation(intensity=0.08, duration=0.85, t="in_out_sine")
        self._pulse_animation.repeat = True
        self._pulse_animation.start(self.pulse)

    def _stop_pulse(self):
        if self._pulse_animation is not None:
            self._pulse_animation.cancel(self.pulse)
            self._pulse_animation = None

    def begin(self, reason, test_mode=False):
        self._cancel_countdown()
        self.pin_pad.reset()
        self.pin_pad.set_enabled_input(True)
        self.reason_label.text = reason
        self.attempts_label.text = ""
        self.lockout_label.text = ""
        self.test_label.text = (
            "TEST MODE  •  NO SOS WILL BE SENT" if test_mode else ""
        )
        self.camera_label.text = ""
        self.gps_label.text = ""
        self.sos_label.text = ""
        self.photo.source = ""
        self.photo.height = 0
        self.photo.opacity = 0
        self._start_pulse()

    def wrong_pin(self, attempts, lock_remaining):
        self.pin_pad.reset()
        if lock_remaining > 0:
            self.attempts_label.text = "INCORRECT PIN  •  ATTEMPT %d" % attempts
            self.pin_pad.set_enabled_input(False)
            self._start_countdown(lock_remaining)
        else:
            self.attempts_label.text = (
                "INCORRECT PIN  •  ATTEMPT %d" % attempts
            )
        self.app.bridge.toast("Incorrect PIN. Alarm remains active.")

    def set_intel(self, key, text, color=MUTED):
        mapping = {
            "camera": self.camera_label,
            "gps": self.gps_label,
            "sos": self.sos_label,
        }
        label = mapping.get(key)
        if label is None:
            return
        label.text = text
        label.color = color

    def show_photo(self, path):
        try:
            self.photo.source = path
            self.photo.height = dp(150)
            self.photo.opacity = 1
        except Exception:
            pass

    def _start_countdown(self, seconds):
        self._cancel_countdown()
        remaining = int(max(1, seconds))

        def _tick(_dt):
            nonlocal remaining
            remaining -= 1
            if remaining <= 0:
                self.lockout_label.text = ""
                self.pin_pad.set_enabled_input(True)
                self._countdown_event = None
                return False
            self.lockout_label.text = (
                "PIN ENTRY LOCKED  •  %d s" % remaining
            )
            return True

        self.lockout_label.text = "PIN ENTRY LOCKED  •  %d s" % remaining
        self._countdown_event = Clock.schedule_interval(_tick, 1.0)

    def _cancel_countdown(self):
        if self._countdown_event is not None:
            try:
                self._countdown_event.cancel()
            except Exception:
                pass
            self._countdown_event = None

    def _on_pin_submitted(self, _pin_pad, pin):
        self.app.handle_alarm_pin(pin)


# ---------------------------------------------------------------------------
# Generic PIN prompt (disarm / settings / power lock)
# ---------------------------------------------------------------------------
class PinPromptScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app

        root = FloatLayout()
        root.add_widget(
            PulseBackground(size_hint=(1, 1), intensity=0.05)
        )
        self.card = Card(
            orientation="vertical",
            size_hint=(0.92, None),
            height=dp(470),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            padding=[dp(18), dp(16), dp(18), dp(16)],
            spacing=dp(8),
        )
        self.title_label = make_label(
            "SECURITY CHECK",
            size=20,
            color=TEXT,
            bold=True,
            height=30,
            halign="center",
        )
        self.subtitle_label = auto_label(
            "",
            size=11,
            color=MUTED,
            halign="center",
        )
        self.status_label = auto_label(
            "", size=11, color=RED, halign="center"
        )
        self.pin_pad = PinPad()
        self.pin_pad.bind(on_submit=self._on_pin_submitted)
        self.cancel_button = CompactButton(
            text="CANCEL",
            accent=MUTED,
            height=dp(42),
        )
        self.cancel_button.bind(on_release=lambda _btn: self.app.cancel_prompt())

        self.card.add_widget(self.title_label)
        self.card.add_widget(self.subtitle_label)
        self.card.add_widget(self.status_label)
        self.card.add_widget(self.pin_pad)
        self.card.add_widget(self.cancel_button)
        root.add_widget(self.card)
        self.add_widget(root)

    def configure(self, title, subtitle, allow_cancel=True, danger=False):
        self.title_label.text = title
        self.title_label.color = RED if danger else TEXT
        self.subtitle_label.text = subtitle
        self.status_label.text = ""
        self.pin_pad.reset()
        self.pin_pad.set_enabled_input(True)
        self.cancel_button.opacity = 1 if allow_cancel else 0
        self.cancel_button.disabled = not allow_cancel
        self.cancel_button.height = dp(42) if allow_cancel else 0
        self.card.border_color = RED if danger else BORDER

    def wrong_pin(self, message):
        self.status_label.text = message
        self.pin_pad.reset()

    def _on_pin_submitted(self, _pin_pad, pin):
        self.app.handle_prompt_pin(pin)


# ---------------------------------------------------------------------------
# Change PIN popup
# ---------------------------------------------------------------------------
class PinChangePopup(Popup):
    def __init__(self, app, **kwargs):
        kwargs.setdefault("title", "Change Owner PIN")
        kwargs.setdefault("size_hint", (0.92, 0.92))
        kwargs.setdefault("auto_dismiss", False)
        kwargs.setdefault("title_color", TEXT)
        kwargs.setdefault("separator_color", BORDER)
        kwargs.setdefault("background_color", BG)
        super().__init__(**kwargs)
        self.app = app
        self.step = 0
        self.temp_pin = ""

        content = BoxLayout(
            orientation="vertical",
            padding=dp(14),
            spacing=dp(8),
        )
        self.prompt = auto_label(
            "Enter your CURRENT 4-digit PIN.",
            size=12,
            color=TEXT,
            bold=True,
            halign="center",
        )
        self.status = auto_label("", size=10, color=RED, halign="center")
        self.pad = PinPad()
        self.pad.bind(on_submit=self._on_submit)
        close = CompactButton(
            text="CLOSE",
            accent=MUTED,
            height=dp(42),
        )
        close.bind(on_release=lambda _btn: self.dismiss())
        content.add_widget(self.prompt)
        content.add_widget(self.status)
        content.add_widget(self.pad)
        content.add_widget(close)
        self.content = content

    def _on_submit(self, _pad, pin):
        if self.step == 0:
            if self.app.store.verify_pin(pin):
                self.step = 1
                self.temp_pin = ""
                self.prompt.text = "Enter the NEW 4-digit PIN."
                self.status.text = ""
                self.pad.reset()
            else:
                self.status.text = "Current PIN is incorrect."
                self.pad.reset()
            return

        if self.step == 1:
            self.temp_pin = pin
            self.step = 2
            self.prompt.text = "Re-enter the NEW PIN to confirm."
            self.status.text = ""
            self.pad.reset()
            return

        if pin != self.temp_pin:
            self.step = 1
            self.temp_pin = ""
            self.prompt.text = "PINs did not match. Enter the NEW PIN again."
            self.status.text = "Confirmation failed."
            self.pad.reset()
            return

        self.app.store.set_pin(pin)
        self.app.store.clear_failed_attempts()
        self.app.bridge.toast("Owner PIN updated.")
        show_toast("Owner PIN updated successfully.")
        self.dismiss()


# ---------------------------------------------------------------------------
# Application controller
# ---------------------------------------------------------------------------
class UltimateThiefGuardApp(App):
    title = "Ultimate Thief Guard"

    def build(self):
        Window.clearcolor = BG
        if platform == "android":
            Window.softinput_mode = "below_target"

        # Core security objects -------------------------------------------
        self.store = SecurityStore(
            os.path.join(self.user_data_dir, "security.json")
        )
        self.alarm_asset = ensure_alarm_wav(self.user_data_dir)
        self.captures_dir = os.path.join(self.user_data_dir, "captures")
        try:
            os.makedirs(self.captures_dir, exist_ok=True)
        except Exception:
            pass

        self.bridge = AndroidBridge()
        self.motion = MotionGuard(
            self._on_motion_detected,
            context=self.bridge.context,
        )
        self.motion.set_sensitivity(self.store.sensitivity)
        self.power_shield = PowerShield(
            self._on_power_event,
            bridge=self.bridge,
        )

        self.alarm_active = False
        self.alarm_test_mode = False
        self.power_lock_pending = False
        self._prompt = None
        self._new_photo_path = None
        self._sos_initial_sent = False
        self._sos_link_sent = False

        # Screens ----------------------------------------------------------
        self.sm = ScreenManager(transition=FadeTransition(duration=0.20))
        self.setup_screen = SetupScreen(self, name="setup")
        self.dashboard = DashboardScreen(self, name="dashboard")
        self.alarm_screen = AlarmScreen(self, name="alarm")
        self.prompt_screen = PinPromptScreen(self, name="pin_prompt")
        self.settings_screen = SettingsScreen(self, name="settings")
        for screen in (
            self.setup_screen,
            self.dashboard,
            self.alarm_screen,
            self.prompt_screen,
            self.settings_screen,
        ):
            self.sm.add_widget(screen)

        self.sm.current = (
            "setup" if not self.store.is_configured else "dashboard"
        )
        Window.bind(on_keyboard=self._on_keyboard)
        Clock.schedule_once(self._post_build, 0.12)
        return self.sm

    # -- Lifecycle ---------------------------------------------------------
    def _post_build(self, _dt):
        self.motion.set_context(self.bridge._ctx())
        if self.store.is_configured:
            # Restore the armed state after an app restart.
            if self.store.armed:
                self._arm_native(show_message=False)
            self.dashboard.refresh()
            if android_available():
                self.bridge.last_known_location(self._on_quick_location)
        else:
            self.setup_screen._refresh_step()

    def on_pause(self):
        # Keep the process alive; a foreground-service build can continue
        # monitoring while the UI is paused.  Without the service, Android may
        # eventually stop normal background apps.
        if self.store.armed and not self.alarm_active:
            self.power_lock_pending = True
        return True

    def on_resume(self):
        if self.alarm_active:
            self.sm.current = "alarm"
            self.bridge.apply_secure_window_flags()
        elif self.store.armed and self.power_lock_pending:
            self._show_power_lock()
        else:
            self.dashboard.refresh()

    def on_stop(self):
        # Stop audio/sensors when the process is actually shutting down.  The
        # armed flag is intentionally preserved so the next launch re-arms.
        try:
            self.bridge.deactivate_alarm()
        except Exception:
            pass
        try:
            self.motion.disarm()
        except Exception:
            pass
        try:
            self.power_shield.release()
        except Exception:
            pass

    # -- Setup / dashboard navigation -------------------------------------
    def complete_setup(self, pin, emergency):
        self.store.set_pin(pin)
        self.store.set_emergency(emergency)
        self.store.set_armed(False)
        self.dashboard.refresh()
        self.sm.current = "dashboard"
        show_toast("Security profile created. System is disarmed.")
        Clock.schedule_once(lambda _dt: self.request_permissions(), 0.8)

    def go_dashboard(self):
        if self.alarm_active:
            self.sm.current = "alarm"
            return
        self.dashboard.refresh()
        self.sm.current = "dashboard"

    def open_settings(self):
        if self.store.armed:

            def _open():
                self.settings_screen.refresh()
                self.sm.current = "settings"

            self.request_pin(
                title="SECURITY CHECK",
                subtitle="Enter the owner PIN to open settings while armed.",
                on_success=_open,
                allow_cancel=True,
                pause_motion=True,
            )
        else:
            self.settings_screen.refresh()
            self.sm.current = "settings"

    # -- Arm / disarm ------------------------------------------------------
    def toggle_arm(self):
        if self.alarm_active:
            return
        if not self.store.is_configured:
            self.sm.current = "setup"
            return
        if self.store.armed:
            self.request_pin(
                title="DISARM SYSTEM",
                subtitle="Enter the owner PIN to disable the motion guard.",
                on_success=self._disarm_after_pin,
                allow_cancel=True,
                pause_motion=True,
            )
        else:
            self.arm_system()

    def arm_system(self):
        if self.store.armed or self.alarm_active:
            return
        if not self.store.emergency:
            show_toast("Set an emergency contact before arming.")
            self.settings_screen.refresh()
            self.sm.current = "settings"
            return

        self.motion.set_sensitivity(self.store.sensitivity)
        started = self._arm_native(show_message=True)
        self.store.set_armed(True)
        self.dashboard.refresh()

        # Ask for runtime permissions (especially SMS/location) when arming.
        if android_available():
            self.request_permissions(silent=True)
            self.bridge.last_known_location(self._on_quick_location)

        if started:
            show_toast("SYSTEM ARMED  •  Motion guard active.")
        else:
            show_toast(
                "Armed, but the motion sensor is unavailable on this build."
            )

    def _arm_native(self, show_message=False):
        self.bridge.apply_secure_window_flags()
        self.power_shield.engage()
        started = self.motion.arm()
        if show_message and started:
            self.dashboard.status_chip.text = "ARMED"
        return started

    def _disarm_after_pin(self):
        self.disarm_system()

    def disarm_system(self):
        self.alarm_active = False
        self.power_lock_pending = False
        self.bridge.deactivate_alarm()
        self.motion.disarm()
        self.power_shield.release()
        self.store.set_armed(False)
        self.store.clear_failed_attempts()
        self.bridge.clear_secure_window_flags()
        self.dashboard.refresh()
        self.sm.current = "dashboard"
        show_toast("SYSTEM DISARMED  •  Guard is offline.")

    # -- PIN prompt --------------------------------------------------------
    def request_pin(
        self,
        title,
        subtitle,
        on_success,
        allow_cancel=True,
        pause_motion=False,
        danger=False,
    ):
        return_screen = self.sm.current
        self._prompt = {
            "on_success": on_success,
            "on_cancel": None,
            "allow_cancel": allow_cancel,
            "pause_motion": pause_motion,
            "return": return_screen,
        }
        if pause_motion and self.store.armed and not self.alarm_active:
            self.motion.set_paused(True)
        self.prompt_screen.configure(
            title=title,
            subtitle=subtitle,
            allow_cancel=allow_cancel,
            danger=danger,
        )
        self.sm.current = "pin_prompt"

    def handle_prompt_pin(self, pin):
        prompt = self._prompt
        if prompt is None:
            self.go_dashboard()
            return

        remaining = self.store.lock_remaining()
        if remaining > 0:
            self.prompt_screen.wrong_pin(
                "Too many attempts. Try again in %d s." % int(remaining + 1)
            )
            return

        if self.store.verify_pin(pin):
            self.store.clear_failed_attempts()
            self._prompt = None
            self.prompt_screen.status_label.text = ""
            callback = prompt.get("on_success")
            if callback is not None:
                try:
                    callback()
                except Exception:
                    pass
            if self.store.armed and prompt.get("pause_motion"):
                self.motion.set_paused(False)
            # Callbacks that did not navigate anywhere return to dashboard.
            if self.sm.current == "pin_prompt":
                self.sm.current = prompt.get("return", "dashboard")
            return

        attempts, lock_remaining = self.store.record_failed_attempt()
        if lock_remaining > 0:
            message = "Incorrect PIN. Locked for %d s." % int(lock_remaining + 1)
        else:
            message = "Incorrect PIN. Attempt %d." % attempts
        self.prompt_screen.wrong_pin(message)
        self.bridge.toast("Incorrect PIN.")

    def cancel_prompt(self):
        prompt = self._prompt or {}
        self._prompt = None
        if self.store.armed and prompt.get("pause_motion"):
            self.motion.set_paused(False)
        self.prompt_screen.pin_pad.reset()
        self.sm.current = prompt.get("return", "dashboard")

    # -- Power shield events ----------------------------------------------
    def _show_power_lock(self):
        if not self.store.armed or self.alarm_active:
            self.power_lock_pending = False
            return

        def _unlock():
            self.power_lock_pending = False
            self.dashboard.refresh()
            self.sm.current = "dashboard"

        self.request_pin(
            title="SYSTEM LOCKED",
            subtitle=(
                "A power/screen event occurred while armed. Enter the owner "
                "PIN to return to the security console."
            ),
            on_success=_unlock,
            allow_cancel=False,
            pause_motion=True,
            danger=True,
        )

    def _on_power_event(self, action):
        action = str(action or "")
        if "SCREEN_OFF" in action:
            if self.store.armed or self.alarm_active:
                self.bridge.apply_secure_window_flags()
        elif "USER_PRESENT" in action:
            if self.alarm_active:
                self.sm.current = "alarm"
                self.bridge.apply_secure_window_flags()
            elif self.store.armed:
                self.power_lock_pending = True
                self._show_power_lock()
        elif "SHUTDOWN" in action:
            if self.store.armed and not self.alarm_test_mode:
                threading.Thread(
                    target=self._send_shutdown_sos,
                    daemon=True,
                ).start()

    def _send_shutdown_sos(self):
        # Shutdown may kill the process within a second, so send immediately
        # from the cached fix instead of waiting for a fresh GPS callback.
        saved = self.store.last_location()
        if saved:
            lat, lon, _ts = saved
            message = (
                "THIEF GUARD: Device is being powered off. Last location: "
                + self.bridge.maps_link(lat, lon)
            )
        else:
            message = (
                "THIEF GUARD: Device is being powered off. "
                "GPS location unavailable."
            )
        self.bridge.send_sms(self.store.emergency, message)

    # -- Motion / alarm ----------------------------------------------------
    def _on_motion_detected(self):
        if not self.store.armed or self.alarm_active:
            return
        if self.sm.current in ("setup", "pin_prompt"):
            return
        self.trigger_alarm("Motion detected by the armed guard.")

    def trigger_alarm(self, reason, test_mode=False):
        if self.alarm_active:
            return
        self.alarm_active = True
        self.alarm_test_mode = bool(test_mode)
        self.power_lock_pending = False
        self._new_photo_path = None
        self._sos_initial_sent = False
        self._sos_link_sent = False

        # Freeze motion triggers while the alarm overlay is active.
        self.motion.suspend_triggers()
        self.bridge.activate_alarm(self.alarm_asset)
        self.bridge.apply_secure_window_flags()
        self.power_shield.engage()
        self.store.set_last_alarm(datetime.now().isoformat(timespec="seconds"))

        self.alarm_screen.begin(reason, test_mode=test_mode)
        self.sm.current = "alarm"

        if test_mode:
            self.alarm_screen.set_intel(
                "camera", "CAMERA   Test mode - capture skipped.", MUTED
            )
            self.alarm_screen.set_intel(
                "gps", "GPS   Test mode - location fetch skipped.", MUTED
            )
            self.alarm_screen.set_intel(
                "sos", "SOS   Test mode - SMS disabled.", AMBER
            )
            return

        threading.Thread(target=self._collect_intel, daemon=True).start()

    def _collect_intel(self):
        self._set_alarm_intel(
            "camera", "CAMERA   Capturing intruder photo...", AMBER
        )
        self._set_alarm_intel("gps", "GPS   Acquiring live location...", AMBER)
        self._set_alarm_intel("sos", "SOS   Waiting for GPS fix...", MUTED)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        photo_path = os.path.join(
            self.captures_dir, "intruder_%s.jpg" % timestamp
        )
        self.bridge.capture_front_photo(
            photo_path, self._on_intruder_photo
        )
        # Send an immediate alert from the last known fix, then follow up with
        # the fresh live fix as soon as the GPS callback returns.
        self.bridge.last_known_location(self._on_initial_sos_location)
        self.bridge.request_location(
            self._on_alarm_location, timeout=22.0
        )

    def _on_initial_sos_location(self, location):
        if not self.alarm_active or self.alarm_test_mode:
            return
        if self._sos_initial_sent:
            return
        if location:
            self.store.set_last_location(location["lat"], location["lon"])
            link = self.bridge.maps_link(location["lat"], location["lon"])
            Clock.schedule_once(
                lambda _dt: self._quick_update_map(location), 0
            )
            self._send_sos(location, link, kind="last")
        else:
            self._send_sos(None, None, kind="acquiring")
        self._sos_initial_sent = True

    def _on_intruder_photo(self, path):
        if not self.alarm_active:
            return
        if path:
            self._new_photo_path = path
            try:
                self.store.set_last_capture(path)
            except Exception:
                pass
            self._set_alarm_intel(
                "camera", "CAMERA   Intruder photo secured.", GREEN
            )
            Clock.schedule_once(
                lambda _dt: self.alarm_screen.show_photo(path), 0
            )
        else:
            self._set_alarm_intel(
                "camera",
                "CAMERA   Unavailable (permission, front camera or screen off).",
                RED,
            )

    def _on_alarm_location(self, location):
        if not self.alarm_active:
            return
        if location:
            self.store.set_last_location(location["lat"], location["lon"])
            link = self.bridge.maps_link(location["lat"], location["lon"])
            self._set_alarm_intel(
                "gps",
                "GPS   %.5f, %.5f  +/- %.0fm"
                % (
                    location["lat"],
                    location["lon"],
                    location.get("accuracy", 0.0),
                ),
                GREEN,
            )
            Clock.schedule_once(
                lambda _dt: self._quick_update_map(location), 0
            )
        else:
            link = None
            self._set_alarm_intel(
                "gps",
                "GPS   No fix available (disabled or permission denied).",
                RED,
            )

        if self.alarm_test_mode:
            return
        if link and not self._sos_link_sent:
            self._send_sos(location, link, kind="live")
            self._sos_link_sent = True
            self._sos_initial_sent = True
        elif not link and not self._sos_initial_sent:
            self._send_sos(None, None, kind="unavailable")
            self._sos_initial_sent = True

    def _send_sos(self, location, link, kind="live"):
        emergency = self.store.emergency
        if not emergency:
            self._set_alarm_intel(
                "sos", "SOS   No emergency number configured.", RED
            )
            return
        if kind == "acquiring":
            message = (
                "THIEF GUARD ALERT: Motion detected. "
                "Acquiring device GPS location..."
            )
        elif link:
            label = "Last known location" if kind == "last" else "Live location"
            message = (
                "THIEF GUARD ALERT: Motion detected. %s: %s" % (label, link)
            )
        else:
            message = (
                "THIEF GUARD ALERT: Motion detected. "
                "GPS location unavailable."
            )
        ok = self.bridge.send_sms(emergency, message)
        if ok:
            if kind == "acquiring":
                text = "SOS   Initial alert sent; acquiring GPS fix."
            elif kind == "last":
                text = "SOS   Alert sent with last known location."
            elif link:
                text = "SOS   Live location sent to %s." % masked_number(
                    emergency
                )
            else:
                text = "SOS   Alert sent; GPS unavailable."
            self._set_alarm_intel("sos", text, GREEN)
        else:
            self._set_alarm_intel(
                "sos", "SOS   SMS failed (check SEND_SMS permission).", RED
            )

    def handle_alarm_pin(self, pin):
        if not self.alarm_active:
            self.go_dashboard()
            return
        remaining = self.store.lock_remaining()
        if remaining > 0:
            self.alarm_screen.wrong_pin(
                self.store.failed_attempts(), remaining
            )
            return
        if self.store.verify_pin(pin):
            self.stop_alarm()
            return
        attempts, lock_remaining = self.store.record_failed_attempt()
        self.alarm_screen.wrong_pin(attempts, lock_remaining)

    def stop_alarm(self):
        self.alarm_active = False
        self.alarm_test_mode = False
        self.power_lock_pending = False
        self.bridge.deactivate_alarm()
        self.motion.disarm()
        self.power_shield.release()
        self.store.set_armed(False)
        self.store.clear_failed_attempts()
        self.bridge.clear_secure_window_flags()
        self.dashboard.refresh()
        self.sm.current = "dashboard"
        show_toast("Alarm disarmed. Evidence remains stored on this device.")

    # -- Settings actions --------------------------------------------------
    def save_emergency(self, number):
        number = normalize_phone(number)
        if not valid_phone(number):
            show_toast("Enter a valid emergency number.")
            return
        self.store.set_emergency(number)
        self.dashboard.refresh()
        self.settings_screen.refresh()
        show_toast("Emergency contact updated.")

    def set_sensitivity(self, value):
        value = max(1, min(10, int(value)))
        self.store.set_sensitivity(value)
        self.motion.set_sensitivity(value)
        self.dashboard.refresh()

    def change_pin(self, new_pin):
        self.store.set_pin(new_pin)
        self.store.clear_failed_attempts()
        show_toast("Owner PIN updated.")

    def show_change_pin(self):
        Popup_instance = PinChangePopup(self)
        Popup_instance.open()

    def confirm_test_alarm(self):
        ConfirmPopup(
            title="Test the alarm?",
            message=(
                "The siren, vibration and PIN overlay will activate. No SOS "
                "SMS is sent in test mode. Enter the owner PIN to stop it."
            ),
            on_confirm=lambda: self.trigger_alarm(
                "Manual test alarm.", test_mode=True
            ),
            confirm_text="START TEST",
            danger=True,
        ).open()

    def confirm_test_sms(self):
        if not self.store.emergency:
            show_toast("Set an emergency contact first.")
            return
        ConfirmPopup(
            title="Send test SMS?",
            message=(
                "A real SMS will be sent to %s. Carrier charges may apply."
                % masked_number(self.store.emergency)
            ),
            on_confirm=self.send_test_sms,
            confirm_text="SEND TEST",
            danger=False,
        ).open()

    def send_test_sms(self):
        emergency = self.store.emergency
        if not emergency:
            show_toast("No emergency number configured.")
            return

        def _on_location(location):
            if location:
                link = self.bridge.maps_link(
                    location["lat"], location["lon"]
                )
                self.store.set_last_location(
                    location["lat"], location["lon"]
                )
                message = (
                    "THIEF GUARD TEST: Monitoring works. Location: " + link
                )
            else:
                message = (
                    "THIEF GUARD TEST: Monitoring works. "
                    "GPS location unavailable."
                )
            ok = self.bridge.send_sms(emergency, message)
            show_toast(
                "Test SMS sent." if ok else "Test SMS failed (permission?)."
            )

        self.bridge.request_location(_on_location, timeout=18.0)

    def find_device(self):
        emergency = self.store.emergency
        if not emergency:
            show_toast("Set an emergency contact first.")
            self.settings_screen.refresh()
            self.sm.current = "settings"
            return
        show_toast("Acquiring live location...")

        def _on_location(location):
            if location:
                self.store.set_last_location(
                    location["lat"], location["lon"]
                )
                link = self.bridge.maps_link(
                    location["lat"], location["lon"]
                )
                message = "THIEF GUARD LOCATE: " + link
                Clock.schedule_once(
                    lambda _dt: self._quick_update_map(location), 0
                )
            else:
                message = (
                    "THIEF GUARD LOCATE: GPS unavailable on the device."
                )
            ok = self.bridge.send_sms(emergency, message)
            show_toast(
                "Location SMS sent."
                if ok
                else "Location SMS failed (check permission)."
            )

        self.bridge.request_location(_on_location, timeout=20.0)

    def request_permissions(self, silent=False):
        if not android_available():
            if not silent:
                show_toast("Android permissions are not available on desktop.")
            return

        def _done(result):
            self.settings_screen.refresh()
            if not silent:
                granted = sum(1 for value in result.values() if value)
                show_toast(
                    "Permission results: %d/%d granted."
                    % (granted, len(result))
                )

        self.bridge.request_permissions(_done)

    def _on_quick_location(self, location):
        if not location:
            return
        self.store.set_last_location(location["lat"], location["lon"])
        self._quick_update_map(location)

    def _quick_update_map(self, location):
        try:
            self.dashboard.update_location(location)
        except Exception:
            pass

    def _set_alarm_intel(self, key, text, color):
        Clock.schedule_once(
            lambda _dt: self.alarm_screen.set_intel(key, text, color), 0
        )

    # -- Android back button ----------------------------------------------
    def _on_keyboard(self, _window, key, *_args):
        if key != 27:  # Android KEYCODE_BACK / desktop Escape
            return False

        current = self.sm.current
        if current in ("alarm", "pin_prompt"):
            if current == "alarm":
                self.bridge.toast("Enter the owner PIN to stop the alarm.")
            return True
        if current == "setup":
            return True
        if current == "settings":
            self.go_dashboard()
            return True
        if current == "dashboard":
            if android_available():
                self.bridge.move_to_back()
                return True
            return False
        return False


if __name__ == "__main__":
    UltimateThiefGuardApp().run()
