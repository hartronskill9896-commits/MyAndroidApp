import hashlib
import hmac
import math
import os
import secrets
import struct
import threading
import time
import wave

from kivy.clock import Clock
from kivy.storage.jsonstore import JsonStore
from kivy.utils import platform

# ---------------------------------------------------------------------------
# Ultimate Thief Guard - core security layer
# ---------------------------------------------------------------------------
# This module deliberately contains no user interface code.  It is shared by
# the foreground Kivy application and (optionally) an Android foreground
# service.  Keeping secure storage, PIN verification and sensor processing in
# one place makes the security-critical code much easier to audit.
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

ANDROID = platform == "android"

if ANDROID:
    try:
        from jnius import PythonJavaClass, autoclass, java_method
    except Exception:
        ANDROID = False


def ensure_alarm_wav(directory):
    path = os.path.join(directory, "guard_alarm.wav")
    if os.path.exists(path) and os.path.getsize(path) > 2000:
        return path
    rate = 22050
    duration = 2.0
    total = int(rate * duration)
    frames = bytearray()
    fade = int(rate * 0.02)
    for index in range(total):
        moment = index / rate
        frequency = 720.0 + 540.0 * (
            0.5 + 0.5 * math.sin(2.0 * math.pi * 2.0 * moment)
        )
        envelope = 1.0
        if index < fade:
            envelope = index / fade
        elif index > total - fade:
            envelope = (total - index) / fade
        sample = int(
            0.60
            * 32767
            * envelope
            * math.sin(2.0 * math.pi * frequency * moment)
        )
        frames += struct.pack("<h", sample)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


class SecurityStore:
    """Small JSON-backed configuration store.

    The owner PIN is never written to disk.  Only a PBKDF2-HMAC-SHA256 digest
    and a random 128-bit salt are persisted.  For a production-grade build you
    should additionally wrap the derived key with the Android Keystore (see the
    hook in ``android_bridge.AndroidBridge``).

    The emergency number is stored in clear text because the Telephony API
    needs the real number when sending an SOS.  Treat the app sandbox as
    sensitive data.
    """

    DEFAULTS = {
        "salt": "",
        "pin_hash": "",
        "emergency": "",
        "armed": False,
        "sensitivity": 5,
        "last_capture": "",
        "last_alarm": "",
        "last_lat": "",
        "last_lon": "",
        "last_fix_ts": 0.0,
        "failed_attempts": 0,
        "lock_until": 0.0,
    }

    def __init__(self, path):
        self.store = JsonStore(path)
        if not self.store.exists("security"):
            self._write(dict(self.DEFAULTS))
        else:
            data = dict(self.store.get("security"))
            changed = False
            for key, value in self.DEFAULTS.items():
                if key not in data:
                    data[key] = value
                    changed = True
            if changed:
                self._write(data)

    def _read(self):
        return dict(self.store.get("security"))

    def _write(self, data):
        self.store.put("security", **data)

    def update(self, **values):
        data = self._read()
        data.update(values)
        self._write(data)

    @property
    def is_configured(self):
        data = self._read()
        return bool(data.get("pin_hash") and data.get("salt"))

    @property
    def emergency(self):
        return self._read().get("emergency", "")

    @property
    def sensitivity(self):
        try:
            return int(self._read().get("sensitivity", 5))
        except Exception:
            return 5

    @property
    def armed(self):
        return bool(self._read().get("armed", False))

    @property
    def last_capture(self):
        return self._read().get("last_capture", "")

    def set_pin(self, pin):
        salt = secrets.token_hex(16)
        digest = self._hash_pin(pin, salt)
        self.update(salt=salt, pin_hash=digest)

    def verify_pin(self, pin):
        data = self._read()
        salt = data.get("salt", "")
        expected = data.get("pin_hash", "")
        if not salt or not expected:
            return False
        return hmac.compare_digest(self._hash_pin(pin, salt), expected)

    @staticmethod
    def _hash_pin(pin, salt):
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            str(pin).encode("utf-8"),
            bytes.fromhex(salt),
            120000,
        )
        return derived.hex()

    def set_emergency(self, number):
        self.update(emergency=number)

    def set_armed(self, value):
        self.update(armed=bool(value))

    def set_sensitivity(self, value):
        self.update(sensitivity=max(1, min(10, int(value))))

    def set_last_capture(self, path):
        self.update(last_capture=path)

    def set_last_alarm(self, timestamp):
        self.update(last_alarm=str(timestamp))

    def set_last_location(self, lat, lon):
        self.update(last_lat=str(lat), last_lon=str(lon), last_fix_ts=time.time())

    def last_location(self):
        data = self._read()
        try:
            return (
                float(data.get("last_lat", "")),
                float(data.get("last_lon", "")),
                float(data.get("last_fix_ts", 0.0)),
            )
        except (TypeError, ValueError):
            return None

    # -- Brute-force mitigation -------------------------------------------
    # The alarm cannot be dismissed while this lockout is active.  The alarm
    # itself does not stop; only PIN entry is temporarily rate-limited.
    def lock_remaining(self):
        try:
            return max(0.0, float(self._read().get("lock_until", 0.0)) - time.time())
        except (TypeError, ValueError):
            return 0.0

    def record_failed_attempt(self):
        data = self._read()
        try:
            attempts = int(data.get("failed_attempts", 0)) + 1
        except (TypeError, ValueError):
            attempts = 1
        lock_until = 0.0
        if attempts >= 5:
            # 30 s, then doubling up to 5 minutes.  Attempts keep counting so
            # a persistent attacker gets progressively slower.
            delay = min(300, 30 * (2 ** min(attempts - 5, 4)))
            lock_until = time.time() + delay
        self.update(failed_attempts=attempts, lock_until=lock_until)
        return attempts, max(0.0, lock_until - time.time())

    def clear_failed_attempts(self):
        self.update(failed_attempts=0, lock_until=0.0)

    def failed_attempts(self):
        try:
            return int(self._read().get("failed_attempts", 0))
        except (TypeError, ValueError):
            return 0


class MotionGuard:
    """Low-pass filtered accelerometer + gyroscope motion detector.

    The first two seconds after arming are used as a calibration window so
    that picking the phone up to arm it never fires the alarm.  A trigger must
    be seen on two consecutive samples to reject single-sample sensor noise.

    Android sensor constants:
      * Sensor.TYPE_ACCELEROMETER == 1
      * Sensor.TYPE_GYROSCOPE     == 4
    """
    def __init__(self, on_trigger, context=None):
        self.on_trigger = on_trigger
        self.context = context
        self.armed = False
        self.triggered = False
        self.paused = False
        self.sensitivity = 5
        self.status = "STANDBY"
        self._gravity = None
        self._accel_hits = 0
        self._gyro_hits = 0
        self._ignore_until = 0.0
        self._last_fire = 0.0
        self._listener = None
        self._sensor_manager = None
        self._plyer = None
        self._lock = threading.Lock()

    def set_context(self, context):
        """Supply the Android Context used by the native sensor manager."""
        self.context = context

    def set_sensitivity(self, value):
        self.sensitivity = max(1, min(10, int(value)))

    def set_paused(self, value):
        """Temporarily ignore motion without disarming the system.

        Used while the PIN dialog is open: handling the phone to type the PIN
        must not re-trigger the siren.  Resuming re-runs calibration so the new
        resting orientation becomes the baseline.
        """
        with self._lock:
            self.paused = bool(value)
            self._gravity = None
            self._accel_hits = 0
            self._gyro_hits = 0
            if not self.paused:
                self._ignore_until = time.monotonic() + 1.5

    def arm(self):
        with self._lock:
            self.armed = True
            self.triggered = False
            self.paused = False
            self._gravity = None
            self._accel_hits = 0
            self._gyro_hits = 0
            self._ignore_until = time.monotonic() + 2.0
            self._last_fire = 0.0
        if ANDROID:
            started = self._start_android()
            self.status = "ACTIVE" if started else "UNAVAILABLE"
        else:
            started = self._start_plyer()
            self.status = "ACTIVE" if started else "SIMULATION"
        return started

    def disarm(self):
        self._stop_android()
        self._stop_plyer()
        with self._lock:
            self.armed = False
            self.triggered = False
            self.paused = False
            self._gravity = None
            self._accel_hits = 0
            self._gyro_hits = 0
        self.status = "STANDBY"

    def suspend_triggers(self):
        with self._lock:
            self.triggered = True

    def _accel_threshold(self):
        return 0.12 + (10 - self.sensitivity) * (1.10 / 9.0)

    def _gyro_threshold(self):
        return 0.12 + (10 - self.sensitivity) * (1.28 / 9.0)

    def _handle_sample(self, x, y, z, sensor_type):
        try:
            now = time.monotonic()
            with self._lock:
                if not self.armed or self.triggered or self.paused:
                    return
                if now < self._ignore_until:
                    self._gravity = [x, y, z]
                    return
                if self._gravity is None:
                    self._gravity = [x, y, z]
                    return
                if sensor_type == 4:
                    magnitude = math.sqrt(x * x + y * y + z * z)
                    threshold = self._gyro_threshold()
                    if magnitude > threshold:
                        self._gyro_hits += 1
                    else:
                        self._gyro_hits = max(0, self._gyro_hits - 1)
                    if self._gyro_hits >= 2:
                        self._fire(now)
                    return
                alpha = 0.85
                self._gravity[0] = (
                    alpha * self._gravity[0] + (1 - alpha) * x
                )
                self._gravity[1] = (
                    alpha * self._gravity[1] + (1 - alpha) * y
                )
                self._gravity[2] = (
                    alpha * self._gravity[2] + (1 - alpha) * z
                )
                dx = x - self._gravity[0]
                dy = y - self._gravity[1]
                dz = z - self._gravity[2]
                delta = math.sqrt(dx * dx + dy * dy + dz * dz)
                if delta > self._accel_threshold():
                    self._accel_hits += 1
                else:
                    self._accel_hits = max(0, self._accel_hits - 1)
                if self._accel_hits >= 2:
                    self._fire(now)
        except Exception:
            return

    def _fire(self, now):
        if now - self._last_fire < 4.0:
            return
        self._last_fire = now
        self.triggered = True
        Clock.schedule_once(lambda _dt: self.on_trigger(), 0)

    def _start_android(self):
        if not ANDROID or self.context is None:
            return False
        try:
            Context = autoclass("android.content.Context")
            SensorManager = autoclass("android.hardware.SensorManager")
            manager = self.context.getSystemService(Context.SENSOR_SERVICE)
            if manager is None:
                return False
            accel = manager.getDefaultSensor(
                SensorManager.SENSOR_TYPE_ACCELEROMETER
            )
            gyro = manager.getDefaultSensor(
                SensorManager.SENSOR_TYPE_GYROSCOPE
            )
            if accel is None and gyro is None:
                return False
            self._stop_android()
            self._sensor_manager = manager
            self._listener = _SensorListener(self)
            rate = SensorManager.SENSOR_DELAY_GAME
            registered = False
            if accel is not None:
                registered = bool(
                    manager.registerListener(self._listener, accel, rate)
                )
            if gyro is not None:
                registered = (
                    bool(manager.registerListener(self._listener, gyro, rate))
                    or registered
                )
            return registered
        except Exception:
            self._stop_android()
            return False

    def _stop_android(self):
        if self._sensor_manager is not None and self._listener is not None:
            try:
                self._sensor_manager.unregisterListener(self._listener)
            except Exception:
                pass
        self._sensor_manager = None
        self._listener = None

    def _start_plyer(self):
        if platform not in ("linux", "android", "ios", "macosx"):
            self._plyer = None
            return False
        try:
            from plyer import accelerometer

            self._plyer = accelerometer
            accelerometer.enable()
            bound = False
            try:
                accelerometer.bind(
                    acceleration=self._plyer_acceleration
                )
                bound = True
            except Exception:
                pass
            if not bound:
                try:
                    accelerometer.on_acceleration = (
                        self._plyer_acceleration
                    )
                except Exception:
                    pass
            return True
        except Exception:
            self._plyer = None
            return False

    def _stop_plyer(self):
        if self._plyer is not None:
            try:
                self._plyer.disable()
            except Exception:
                pass
            self._plyer = None

    def _plyer_acceleration(self, *args):
        values = None
        for value in reversed(args):
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                values = value
                break
        if values is None and len(args) >= 3:
            values = (args[-3], args[-2], args[-1])
        if values is None:
            return
        try:
            self._handle_sample(
                float(values[0]),
                float(values[1]),
                float(values[2]),
                1,
            )
        except Exception:
            return


if ANDROID:

    class _SensorListener(PythonJavaClass):
        __javainterfaces__ = ["android/hardware/SensorEventListener"]
        __javacontext__ = "app"

        def __init__(self, guard):
            super().__init__()
            self.guard = guard

        @java_method("(Landroid/hardware/SensorEvent;)V")
        def onSensorChanged(self, event):
            try:
                values = event.values
                self.guard._handle_sample(
                    float(values[0]),
                    float(values[1]),
                    float(values[2]),
                    int(event.sensor.getType()),
                )
            except Exception:
                pass

        @java_method("(Landroid/hardware/Sensor;I)V")
        def onAccuracyChanged(self, sensor, accuracy):
            pass
