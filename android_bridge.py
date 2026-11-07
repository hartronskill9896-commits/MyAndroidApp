"""Android integration layer for Ultimate Thief Guard.

Every Java/Android interaction lives in this module.  The UI and the security
core only use the platform-neutral methods of :class:`AndroidBridge`.

Important
---------
* All native calls fail soft: a missing front camera or denied permission must
  never prevent the siren or PIN overlay from working.
* PyJNIus/Android classes are imported lazily so the app still runs on a PC.
* Search for ``HOOK`` to find the places where a production build can plug in
  CameraX, Android Keystore, a foreground service, a device-owner policy, or
  an Accessibility Service.
"""

import os
import threading
import time

from kivy.clock import Clock
from kivy.utils import platform

# ---------------------------------------------------------------------------
# PyJNIus is only available inside a python-for-android APK.
# ---------------------------------------------------------------------------
ANDROID = platform == "android"
_PYJNIUS_OK = False
mActivity = None

if ANDROID:
    try:
        from jnius import (  # type: ignore
            PythonJavaClass,
            autoclass,
            cast,
            jarray,
            java_method,
        )
        from android import mActivity  # type: ignore

        _PYJNIUS_OK = True
    except Exception:
        _PYJNIUS_OK = False
        mActivity = None

ANDROID = ANDROID and _PYJNIUS_OK


def android_available():
    """True only inside a working Android/PyJNIus build."""
    return ANDROID


def _java_sdk_int():
    if not ANDROID:
        return 0
    try:
        version = autoclass("android.os.Build$VERSION")
        return int(version.SDK_INT)
    except Exception:
        return 0


def _permission_granted(permission):
    if not ANDROID:
        return False
    try:
        from android.permissions import check_permission  # type: ignore

        return bool(check_permission(permission))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# AndroidBridge
# ---------------------------------------------------------------------------
class AndroidBridge:
    """Facade over Android audio, telephony, location, camera and window APIs.

    ``context`` is normally the Activity.  A Service may pass its own Context
    so audio, SMS and location still work in the background.
    """

    def __init__(self, context=None):
        self.context = context or mActivity
        self.activity = mActivity
        self._player = None
        self._vibrator = None
        self._wake_lock = None
        self._saved_audio = None
        self._desktop_sound = None
        self._desktop_stop = None
        self._desktop_thread = None
        self._lock = threading.RLock()

    # -- Context / UI ------------------------------------------------------
    def set_activity(self, activity):
        self.activity = activity
        if self.context is None:
            self.context = activity

    def _ctx(self):
        return self.context or self.activity

    def _run_on_ui(self, callback):
        if not ANDROID:
            callback()
            return
        try:
            activity = self.activity or self._ctx()
            if activity is None:
                return
            activity.runOnUiThread(_UiRunnable(callback))
        except Exception:
            pass

    def toast(self, message):
        message = str(message)
        if not ANDROID:
            print("[Ultimate Thief Guard] %s" % message)
            return

        def _show():
            try:
                Toast = autoclass("android.widget.Toast")
                Toast.makeText(self._ctx(), message, Toast.LENGTH_LONG).show()
            except Exception:
                pass

        self._run_on_ui(_show)

    def move_to_back(self):
        if not ANDROID:
            return
        try:
            self.activity.moveTaskToBack(True)
        except Exception:
            pass

    # -- Runtime permissions ----------------------------------------------
    def request_permissions(self, callback=None):
        """Request camera, SMS, location, phone state and notification access."""
        if not ANDROID:
            if callback:
                Clock.schedule_once(lambda _dt: callback({}), 0)
            return

        def _request():
            try:
                from android.permissions import (  # type: ignore
                    Permission,
                    request_permissions,
                )

                wanted = []
                for name in (
                    "CAMERA",
                    "SEND_SMS",
                    "ACCESS_FINE_LOCATION",
                    "ACCESS_COARSE_LOCATION",
                    "READ_PHONE_STATE",
                    "POST_NOTIFICATIONS",
                ):
                    value = getattr(Permission, name, None)
                    if value:
                        wanted.append(value)

                def _done(permissions, grants):
                    result = {}
                    try:
                        for key, value in zip(permissions, grants):
                            result[str(key)] = bool(value)
                    except Exception:
                        pass
                    if callback:
                        Clock.schedule_once(lambda _dt: callback(result), 0)

                request_permissions(wanted, _done)
            except Exception:
                if callback:
                    Clock.schedule_once(lambda _dt: callback({}), 0)

        self._run_on_ui(_request)

    def permission_report(self):
        if not ANDROID:
            return "Desktop simulation mode - Android permissions not required."
        checks = {
            "Camera": "CAMERA",
            "SMS": "SEND_SMS",
            "Location": "ACCESS_FINE_LOCATION",
        }
        parts = []
        for label, name in checks.items():
            constant = self._permission_constant(name)
            value = getattr(constant, "value", constant)
            parts.append(
                "%s: %s"
                % (label, "GRANTED" if _permission_granted(value) else "DENIED")
            )
        return " | ".join(parts)

    @staticmethod
    def _permission_constant(name):
        try:
            from android.permissions import Permission  # type: ignore

            return getattr(Permission, name)
        except Exception:
            return name

    def request_overlay_permission(self):
        """Open the 'Display over other apps' settings screen.

        HOOK: after this special permission is granted, a floating view could
        cover the system power dialog.  It is optional and unnecessary for the
        normal in-app PIN shield.
        """
        if not ANDROID:
            return
        try:
            Intent = autoclass("android.content.Intent")
            Settings = autoclass("android.provider.Settings")
            Uri = autoclass("android.net.Uri")
            package = self._ctx().getPackageName()
            intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + package),
            )
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            self._ctx().startActivity(intent)
        except Exception:
            self.toast("Overlay permission screen is unavailable.")

    # -- AudioManager / siren ---------------------------------------------
    def _audio_manager(self):
        Context = autoclass("android.content.Context")
        AudioManager = autoclass("android.media.AudioManager")
        return cast(
            AudioManager,
            self._ctx().getSystemService(Context.AUDIO_SERVICE),
        )

    def _save_audio_state(self):
        if not ANDROID:
            return
        try:
            AudioManager = autoclass("android.media.AudioManager")
            manager = self._audio_manager()
            streams = [
                AudioManager.STREAM_MUSIC,
                AudioManager.STREAM_ALARM,
                AudioManager.STREAM_RING,
                AudioManager.STREAM_NOTIFICATION,
                AudioManager.STREAM_SYSTEM,
            ]
            state = {"ringer": manager.getRingerMode(), "streams": {}}
            for stream in streams:
                try:
                    state["streams"][stream] = manager.getStreamVolume(stream)
                except Exception:
                    pass
            self._saved_audio = state
        except Exception:
            self._saved_audio = None

    def _force_audio_max(self):
        """Push ringer to normal and every stream to 100%.

        Do Not Disturb / notification-policy access can still suppress ringer
        changes; the siren itself uses ``USAGE_ALARM`` so it is usually heard
        even then.
        """
        if not ANDROID:
            return
        try:
            AudioManager = autoclass("android.media.AudioManager")
            manager = self._audio_manager()
            try:
                manager.setRingerMode(AudioManager.RINGER_MODE_NORMAL)
            except Exception:
                pass
            for stream in (
                AudioManager.STREAM_MUSIC,
                AudioManager.STREAM_ALARM,
                AudioManager.STREAM_RING,
                AudioManager.STREAM_NOTIFICATION,
                AudioManager.STREAM_SYSTEM,
            ):
                try:
                    manager.setStreamVolume(
                        stream, manager.getStreamMaxVolume(stream), 0
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _restore_audio_state(self):
        if not ANDROID or not self._saved_audio:
            return
        try:
            manager = self._audio_manager()
            for stream, volume in self._saved_audio.get("streams", {}).items():
                try:
                    manager.setStreamVolume(stream, volume, 0)
                except Exception:
                    pass
            try:
                manager.setRingerMode(self._saved_audio.get("ringer"))
            except Exception:
                pass
        except Exception:
            pass
        finally:
            self._saved_audio = None

    def _start_android_alarm(self, asset_path):
        try:
            MediaPlayer = autoclass("android.media.MediaPlayer")
            AudioAttributes = autoclass("android.media.AudioAttributes")
            player = MediaPlayer()
            attributes = (
                AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()
            )
            player.setAudioAttributes(attributes)
            player.setDataSource(str(asset_path))
            player.setLooping(True)
            player.setVolume(1.0, 1.0)
            player.prepare()
            player.start()
            with self._lock:
                self._player = player
            return True
        except Exception:
            return False

    def _stop_android_alarm(self):
        with self._lock:
            player = self._player
            self._player = None
        if player is None:
            return
        try:
            if player.isPlaying():
                player.stop()
        except Exception:
            pass
        try:
            player.release()
        except Exception:
            pass

    def _start_desktop_alarm(self, asset_path):
        try:
            from kivy.core.audio import SoundLoader

            sound = SoundLoader.load(asset_path)
            if sound is not None:
                sound.loop = True
                sound.play()
                self._desktop_sound = sound
                return True
        except Exception:
            pass

        if platform != "win":
            return False
        try:
            import winsound
        except Exception:
            return False

        stop_event = threading.Event()
        self._desktop_stop = stop_event

        def _siren():
            while not stop_event.is_set():
                for frequency in (780, 1180, 880, 1420):
                    if stop_event.is_set():
                        break
                    try:
                        winsound.Beep(frequency, 220)
                    except Exception:
                        return

        self._desktop_thread = threading.Thread(target=_siren, daemon=True)
        self._desktop_thread.start()
        return True

    def _stop_desktop_alarm(self):
        sound = self._desktop_sound
        self._desktop_sound = None
        if sound is not None:
            try:
                sound.stop()
            except Exception:
                pass
        if self._desktop_stop is not None:
            self._desktop_stop.set()
            self._desktop_stop = None

    def _start_vibration(self):
        if not ANDROID:
            return False
        try:
            Context = autoclass("android.content.Context")
            Vibrator = autoclass("android.os.Vibrator")
            VibrationEffect = autoclass("android.os.VibrationEffect")
            vibrator = cast(
                Vibrator,
                self._ctx().getSystemService(Context.VIBRATOR_SERVICE),
            )
            if vibrator is None or not vibrator.hasVibrator():
                return False
            pattern = [0, 650, 250, 900, 350]
            if _java_sdk_int() >= 26:
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, 0))
            else:
                vibrator.vibrate(pattern, 0)
            self._vibrator = vibrator
            return True
        except Exception:
            return False

    def _stop_vibration(self):
        vibrator = self._vibrator
        self._vibrator = None
        if vibrator is not None:
            try:
                vibrator.cancel()
            except Exception:
                pass

    def _acquire_wake_lock(self):
        if not ANDROID:
            return
        try:
            Context = autoclass("android.content.Context")
            PowerManager = autoclass("android.os.PowerManager")
            manager = cast(
                PowerManager,
                self._ctx().getSystemService(Context.POWER_SERVICE),
            )
            lock = manager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK
                | PowerManager.ACQUIRE_CAUSES_WAKEUP,
                "UltimateThiefGuard::AlarmWakeLock",
            )
            lock.acquire(10 * 60 * 1000)
            self._wake_lock = lock
        except Exception:
            pass

    def _release_wake_lock(self):
        lock = self._wake_lock
        self._wake_lock = None
        if lock is not None:
            try:
                if lock.isHeld():
                    lock.release()
            except Exception:
                pass

    def activate_alarm(self, asset_path):
        with self._lock:
            self._save_audio_state()
            self._force_audio_max()
            self._acquire_wake_lock()
            if ANDROID:
                self._start_android_alarm(asset_path)
            else:
                self._start_desktop_alarm(asset_path)
            self._start_vibration()
            self.apply_secure_window_flags()
        return True

    def deactivate_alarm(self):
        with self._lock:
            if ANDROID:
                self._stop_android_alarm()
            else:
                self._stop_desktop_alarm()
            self._stop_vibration()
            self._restore_audio_state()
            self._release_wake_lock()
            self.clear_secure_window_flags()

    # -- Window / power shield --------------------------------------------
    def apply_secure_window_flags(self):
        """Keep the screen awake and show the activity over the keyguard.

        A normal app cannot intercept the hardware power button.  This is the
        strongest public-API approximation; the PIN overlay appears whenever
        the activity resumes after a power/screen event.

        HOOK: true power-off blocking requires a device-owner/lock-task build
        or an Accessibility Service that dismisses the global power dialog.
        """
        if not ANDROID:
            return

        def _apply():
            try:
                activity = self.activity or self._ctx()
                LayoutParams = autoclass("android.view.WindowManager$LayoutParams")
                flags = 0
                for name in (
                    "FLAG_KEEP_SCREEN_ON",
                    "FLAG_TURN_SCREEN_ON",
                    "FLAG_SHOW_WHEN_LOCKED",
                    "FLAG_DISMISS_KEYGUARD",
                ):
                    flags |= getattr(LayoutParams, name, 0)
                activity.getWindow().addFlags(flags)
                try:
                    activity.setShowWhenLocked(True)
                except Exception:
                    pass
                try:
                    activity.setTurnScreenOn(True)
                except Exception:
                    pass
            except Exception:
                pass

        self._run_on_ui(_apply)

    def clear_secure_window_flags(self):
        if not ANDROID:
            return

        def _clear():
            try:
                activity = self.activity or self._ctx()
                LayoutParams = autoclass("android.view.WindowManager$LayoutParams")
                flags = 0
                for name in (
                    "FLAG_KEEP_SCREEN_ON",
                    "FLAG_TURN_SCREEN_ON",
                    "FLAG_SHOW_WHEN_LOCKED",
                    "FLAG_DISMISS_KEYGUARD",
                ):
                    flags |= getattr(LayoutParams, name, 0)
                activity.getWindow().clearFlags(flags)
                try:
                    activity.setShowWhenLocked(False)
                except Exception:
                    pass
                try:
                    activity.setTurnScreenOn(False)
                except Exception:
                    pass
            except Exception:
                pass

        self._run_on_ui(_clear)

    def bring_to_front(self):
        if not ANDROID:
            return

        def _front():
            try:
                Intent = autoclass("android.content.Intent")
                package = self._ctx().getPackageName()
                intent = (
                    self._ctx()
                    .getPackageManager()
                    .getLaunchIntentForPackage(package)
                )
                if intent is None:
                    return
                intent.addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                )
                self._ctx().startActivity(intent)
            except Exception:
                pass

        self._run_on_ui(_front)

    # -- GPS ---------------------------------------------------------------
    def request_location(self, callback, timeout=20.0):
        """Fetch one GPS/network fix asynchronously.

        Callback receives ``None`` or
        ``{'lat', 'lon', 'accuracy', 'provider', 'ts'}`` on the Kivy thread.
        """
        return LocationService(self).request_once(callback, timeout=timeout)

    def last_known_location(self, callback):
        if not ANDROID:
            Clock.schedule_once(lambda _dt: callback(None), 0)
            return
        LocationService(self).last_known(callback)

    @staticmethod
    def maps_link(lat, lon):
        return "https://maps.google.com/?q=%.6f,%.6f" % (lat, lon)

    # -- Telephony ---------------------------------------------------------
    def send_sms(self, number, message):
        number = str(number or "").strip()
        message = str(message or "")
        if not number or not message:
            return False
        if not ANDROID:
            print("[SMS -> %s] %s" % (number, message))
            return True
        try:
            SmsManager = autoclass("android.telephony.SmsManager")
            manager = SmsManager.getDefault()
            if len(message) > 160:
                parts = manager.divideMessage(message)
                manager.sendMultipartTextMessage(
                    number, None, parts, None, None
                )
            else:
                manager.sendTextMessage(number, None, message, None, None)
            return True
        except Exception:
            # HOOK: use an SMS intent if SEND_SMS was permanently denied.
            return False

    def dial_number(self, number):
        if not ANDROID:
            return False
        try:
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            intent = Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + str(number)))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            self._ctx().startActivity(intent)
            return True
        except Exception:
            return False

    # -- Camera ------------------------------------------------------------
    def capture_front_photo(self, output_path, callback=None):
        """Capture a JPEG from the front camera with no visible preview.

        Camera2 + ImageReader is used.  It is the only public API that can take
        a photo without opening a camera UI.

        HOOK: replace with CameraX ``ImageCapture`` for more device coverage
        (requires AndroidX AARs in the p4a recipe).  ML Kit can be added for
        face detection after capture.
        """
        if not ANDROID:
            if callback:
                Clock.schedule_once(lambda _dt: callback(None), 0)
            return False
        try:
            return Camera2Capturer(self._ctx()).capture(
                output_path, callback
            )
        except Exception:
            if callback:
                Clock.schedule_once(lambda _dt: callback(None), 0)
            return False


# ---------------------------------------------------------------------------
# One-shot location
# ---------------------------------------------------------------------------
class LocationService:
    def __init__(self, bridge):
        self.bridge = bridge
        self._manager = None
        self._listener = None
        self._timeout_event = None
        self._callback = None
        self._done = False
        self._running = False
        self._lock = threading.Lock()

    def _last_known(self, manager):
        try:
            LocationManager = autoclass("android.location.LocationManager")
            for provider in (
                LocationManager.GPS_PROVIDER,
                LocationManager.NETWORK_PROVIDER,
            ):
                try:
                    location = manager.getLastKnownLocation(provider)
                    if location is not None:
                        return location
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def last_known(self, callback):
        if not ANDROID:
            Clock.schedule_once(lambda _dt: callback(None), 0)
            return
        try:
            Context = autoclass("android.content.Context")
            LocationManager = autoclass("android.location.LocationManager")
            manager = cast(
                LocationManager,
                self.bridge._ctx().getSystemService(Context.LOCATION_SERVICE),
            )
            result = self._serialize(self._last_known(manager))
        except Exception:
            result = None
        Clock.schedule_once(lambda _dt: callback(result), 0)

    def request_once(self, callback, timeout=20.0):
        if not ANDROID:
            Clock.schedule_once(lambda _dt: callback(None), 0.1)
            return False

        with self._lock:
            if self._running:
                return False
            self._running = True
            self._done = False
            self._callback = callback

        last = None
        try:
            Context = autoclass("android.content.Context")
            LocationManager = autoclass("android.location.LocationManager")
            Looper = autoclass("android.os.Looper")

            manager = cast(
                LocationManager,
                self.bridge._ctx().getSystemService(Context.LOCATION_SERVICE),
            )
            self._manager = manager
            last = self._last_known(manager)

            provider = None
            try:
                if manager.isProviderEnabled(LocationManager.GPS_PROVIDER):
                    provider = LocationManager.GPS_PROVIDER
                elif manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER):
                    provider = LocationManager.NETWORK_PROVIDER
            except Exception:
                pass

            if provider is None:
                self._finish(last, callback)
                return False

            listener = LocationListener(self)
            self._listener = listener
            self._timeout_event = Clock.schedule_once(
                lambda _dt: self._finish(last, callback), timeout
            )
            manager.requestLocationUpdates(
                provider,
                1000,
                0.0,
                listener,
                Looper.getMainLooper(),
            )
            return True
        except Exception:
            self._finish(last, callback)
            return False

    def _on_location(self, location):
        self._finish(location, self._callback)

    def _serialize(self, location):
        if location is None:
            return None
        try:
            return {
                "lat": float(location.getLatitude()),
                "lon": float(location.getLongitude()),
                "accuracy": float(location.getAccuracy()),
                "provider": str(location.getProvider()),
                "ts": time.time(),
            }
        except Exception:
            return None

    def _finish(self, location, callback):
        with self._lock:
            if self._done:
                return
            self._done = True
            self._running = False
            if callback is None:
                callback = self._callback
            self._callback = None

        if self._timeout_event is not None:
            try:
                self._timeout_event.cancel()
            except Exception:
                pass
            self._timeout_event = None
        if self._manager is not None and self._listener is not None:
            try:
                self._manager.removeUpdates(self._listener)
            except Exception:
                pass
        self._manager = None
        self._listener = None

        result = self._serialize(location)
        if callback is not None:
            Clock.schedule_once(lambda _dt: callback(result), 0)


# ---------------------------------------------------------------------------
# Power/screen events
# ---------------------------------------------------------------------------
class PowerShield:
    """Dynamic receiver for screen-off, user-present and shutdown events.

    ACTION_SCREEN_OFF re-applies the secure window flags; ACTION_USER_PRESENT
    raises the PIN lock overlay; ACTION_SHUTDOWN can send a final SOS.

    HOOK: stock Android does not let an app block the power menu.  See
    AndroidBridge.apply_secure_window_flags.
    """

    def __init__(self, callback, bridge=None):
        self.callback = callback
        self.bridge = bridge
        self._receiver = None
        self._engaged = False

    def engage(self):
        if not ANDROID or self._engaged:
            return False
        try:
            Intent = autoclass("android.content.Intent")
            IntentFilter = autoclass("android.content.IntentFilter")
            context = self.bridge._ctx() if self.bridge else mActivity
            filter = IntentFilter()
            filter.addAction(Intent.ACTION_SCREEN_OFF)
            filter.addAction(Intent.ACTION_USER_PRESENT)
            try:
                filter.addAction(Intent.ACTION_SHUTDOWN)
            except Exception:
                pass
            receiver = PowerStateReceiver(self.callback)
            try:
                context.registerReceiver(receiver, filter)
            except Exception:
                fallback = IntentFilter()
                fallback.addAction(Intent.ACTION_SCREEN_OFF)
                fallback.addAction(Intent.ACTION_USER_PRESENT)
                context.registerReceiver(receiver, fallback)
            self._receiver = receiver
            self._engaged = True
            return True
        except Exception:
            return False

    def release(self):
        if not ANDROID or not self._engaged:
            return
        try:
            context = self.bridge._ctx() if self.bridge else mActivity
            context.unregisterReceiver(self._receiver)
        except Exception:
            pass
        self._receiver = None
        self._engaged = False


# ---------------------------------------------------------------------------
# Native Java proxies (valid only inside an APK)
# ---------------------------------------------------------------------------
if ANDROID:

    class _UiRunnable(PythonJavaClass):
        __javainterfaces__ = ["java/lang/Runnable"]
        __javacontext__ = "app"

        def __init__(self, callback):
            super().__init__()
            self.callback = callback

        @java_method("()V")
        def run(self):
            try:
                self.callback()
            except Exception:
                pass

    class LocationListener(PythonJavaClass):
        __javainterfaces__ = ["android/location/LocationListener"]
        __javacontext__ = "app"

        def __init__(self, service):
            super().__init__()
            self.service = service

        @java_method("(Landroid/location/Location;)V")
        def onLocationChanged(self, location):
            try:
                self.service._on_location(location)
            except Exception:
                pass

        @java_method("(Ljava/lang/String;ILandroid/os/Bundle;)V")
        def onStatusChanged(self, provider, status, extras):
            pass

        @java_method("(Ljava/lang/String;)V")
        def onProviderEnabled(self, provider):
            pass

        @java_method("(Ljava/lang/String;)V")
        def onProviderDisabled(self, provider):
            pass

    class PowerStateReceiver(PythonJavaClass):
        __javainterfaces__ = ["android/content/BroadcastReceiver"]
        __javacontext__ = "app"

        def __init__(self, callback):
            super().__init__()
            self.callback = callback

        @java_method("(Landroid/content/Context;Landroid/content/Intent;)V")
        def onReceive(self, context, intent):
            try:
                action = str(intent.getAction())
                Clock.schedule_once(lambda _dt: self.callback(action), 0)
            except Exception:
                pass

    class CameraJob:
        def __init__(self, output_path, callback):
            self.output_path = output_path
            self.callback = callback
            self.done = False
            self.lock = threading.Lock()
            self.camera = None
            self.session = None
            self.reader = None
            self.handler = None
            self.thread = None
            self.open_callback = None
            self.session_callback = None
            self.image_listener = None
            self.timeout = None

        def finish(self, success):
            with self.lock:
                if self.done:
                    return
                self.done = True

            if self.timeout is not None and self.handler is not None:
                try:
                    self.handler.removeCallbacks(self.timeout)
                except Exception:
                    pass
            try:
                if self.session is not None:
                    self.session.close()
            except Exception:
                pass
            try:
                if self.camera is not None:
                    self.camera.close()
            except Exception:
                pass
            try:
                if self.reader is not None:
                    self.reader.close()
            except Exception:
                pass
            try:
                if self.thread is not None:
                    self.thread.quitSafely()
            except Exception:
                pass
            if self.callback is not None:
                Clock.schedule_once(
                    lambda _dt: self.callback(
                        self.output_path if success else None
                    ),
                    0,
                )

    class CameraOpenCallback(PythonJavaClass):
        __javainterfaces__ = ["android/hardware/camera2/CameraDevice$StateCallback"]
        __javacontext__ = "app"

        def __init__(self, job):
            super().__init__()
            self.job = job

        @java_method("(Landroid/hardware/camera2/CameraDevice;)V")
        def onOpened(self, camera):
            try:
                self.job.camera = camera
                ArrayList = autoclass("java.util.ArrayList")
                surfaces = ArrayList()
                surfaces.add(self.job.reader.getSurface())
                self.job.session_callback = CameraSessionCallback(self.job)
                camera.createCaptureSession(
                    surfaces,
                    self.job.session_callback,
                    self.job.handler,
                )
            except Exception:
                self.job.finish(False)

        @java_method("(Landroid/hardware/camera2/CameraDevice;I)V")
        def onDisconnected(self, camera):
            self.job.finish(False)

        @java_method("(Landroid/hardware/camera2/CameraDevice;I)V")
        def onError(self, camera, error):
            self.job.finish(False)

    class CameraSessionCallback(PythonJavaClass):
        __javainterfaces__ = [
            "android/hardware/camera2/CameraCaptureSession$StateCallback"
        ]
        __javacontext__ = "app"

        def __init__(self, job):
            super().__init__()
            self.job = job

        @java_method("(Landroid/hardware/camera2/CameraCaptureSession;)V")
        def onConfigured(self, session):
            try:
                self.job.session = session
                CameraDevice = autoclass("android.hardware.camera2.CameraDevice")
                request = self.job.camera.createCaptureRequest(
                    CameraDevice.TEMPLATE_STILL_CAPTURE
                )
                request.addTarget(self.job.reader.getSurface())
                session.capture(request.build(), None, self.job.handler)
            except Exception:
                self.job.finish(False)

        @java_method("(Landroid/hardware/camera2/CameraCaptureSession;)V")
        def onConfigureFailed(self, session):
            self.job.finish(False)

    class CameraImageListener(PythonJavaClass):
        __javainterfaces__ = [
            "android/media/ImageReader$OnImageAvailableListener"
        ]
        __javacontext__ = "app"

        def __init__(self, job):
            super().__init__()
            self.job = job

        @java_method("(Landroid/media/ImageReader;)V")
        def onImageAvailable(self, reader):
            image = None
            try:
                image = reader.acquireLatestImage()
                if image is None:
                    return
                buffer = image.getPlanes()[0].getBuffer()
                size = buffer.remaining()
                payload = jarray("b", [0] * size)
                buffer.get(payload)
                with open(self.job.output_path, "wb") as handle:
                    handle.write(
                        bytes((int(value) & 0xFF) for value in payload)
                    )
                self.job.finish(True)
            except Exception:
                self.job.finish(False)
            finally:
                if image is not None:
                    try:
                        image.close()
                    except Exception:
                        pass

    class CameraTimeout(PythonJavaClass):
        __javainterfaces__ = ["java/lang/Runnable"]
        __javacontext__ = "app"

        def __init__(self, job):
            super().__init__()
            self.job = job

        @java_method("()V")
        def run(self):
            self.job.finish(False)

    class Camera2Capturer:
        """Camera2 + ImageReader silent still capture."""

        def __init__(self, context=None):
            self.context = context

        def capture(self, output_path, callback=None):
            try:
                Context = autoclass("android.content.Context")
                CameraManager = autoclass("android.hardware.camera2.CameraManager")
                CameraCharacteristics = autoclass(
                    "android.hardware.camera2.CameraCharacteristics"
                )
                ImageReader = autoclass("android.media.ImageReader")
                ImageFormat = autoclass("android.graphics.ImageFormat")
                HandlerThread = autoclass("android.os.HandlerThread")
                Handler = autoclass("android.os.Handler")

                context = self.context or mActivity
                if context is None:
                    if callback:
                        Clock.schedule_once(lambda _dt: callback(None), 0)
                    return False

                manager = cast(
                    CameraManager,
                    context.getSystemService(Context.CAMERA_SERVICE),
                )
                front_id = None
                for camera_id in manager.getCameraIdList():
                    try:
                        characteristics = manager.getCameraCharacteristics(camera_id)
                        facing = characteristics.get(
                            CameraCharacteristics.LENS_FACING
                        )
                        try:
                            facing_value = int(facing.intValue())
                        except Exception:
                            facing_value = int(facing)
                        if facing_value == int(
                            CameraCharacteristics.LENS_FACING_FRONT
                        ):
                            front_id = camera_id
                            break
                    except Exception:
                        continue
                if front_id is None:
                    if callback:
                        Clock.schedule_once(lambda _dt: callback(None), 0)
                    return False

                directory = os.path.dirname(output_path)
                if directory and not os.path.isdir(directory):
                    os.makedirs(directory, exist_ok=True)

                job = CameraJob(output_path, callback)
                job.reader = ImageReader.newInstance(
                    1280, 720, ImageFormat.JPEG, 2
                )

                thread = HandlerThread("UltimateThiefGuardCamera")
                thread.start()
                handler = Handler(thread.getLooper())
                job.thread = thread
                job.handler = handler

                job.image_listener = CameraImageListener(job)
                job.reader.setOnImageAvailableListener(
                    job.image_listener, handler
                )
                job.open_callback = CameraOpenCallback(job)
                job.timeout = CameraTimeout(job)
                handler.postDelayed(job.timeout, 12000)
                manager.openCamera(front_id, job.open_callback, handler)
                return True
            except Exception:
                if callback:
                    Clock.schedule_once(lambda _dt: callback(None), 0)
                return False

else:
    class Camera2Capturer:
        def __init__(self, context=None):
            self.context = context

        def capture(self, output_path, callback=None):
            if callback:
                Clock.schedule_once(lambda _dt: callback(None), 0)
            return False

    class PowerStateReceiver:
        pass

    class LocationListener:
        pass
