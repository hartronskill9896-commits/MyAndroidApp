[app]

title = Ultimate Thief Guard
package.name = ultimatethiefguard
package.domain = org.ultimateguard

source.dir = .
source.include_exts = py,png,jpg,jpeg,atlas,json,wav
source.exclude_dirs = bin,.buildozer,__pycache__,captures

version = 1.0.0

# Kivy + PyJNIus + the p4a `android` bridge.  Plyer is used only as a desktop
# accelerometer fallback; native SensorManager is used on Android.
requirements = python3,kivy==2.3.1,plyer,pyjnius,android

orientation = portrait
fullscreen = 1
android.presplash_color = #05070F
android.allow_backup = False
android.private_storage = True

android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = arm64-v8a, armeabi-v7a
android.release_abis = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

# Runtime permissions requested by AndroidBridge.request_permissions().
# SYSTEM_ALERT_WINDOW is a *special* permission; the user must grant it from
# the "Display over other apps" settings page (optional power-shield hook).
android.permissions = CAMERA,SEND_SMS,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION,READ_PHONE_STATE,VIBRATE,WAKE_LOCK,MODIFY_AUDIO_SETTINGS,POST_NOTIFICATIONS,FOREGROUND_SERVICE,FOREGROUND_SERVICE_CAMERA,FOREGROUND_SERVICE_LOCATION,SYSTEM_ALERT_WINDOW

p4a.bootstrap = sdl2
p4a.branch = master

# ---------------------------------------------------------------------------
# Optional: background guarding when the UI is not in front.
#
# p4a services run in a separate Python process.  Add a service module (for
# example services/guard_service.py) that reads the saved configuration from
# the app's files directory, monitors SensorManager, and launches the main
# activity / raises a full-screen notification when motion is detected.
# Then enable the line below (Android 14+ requires a foregroundServiceType):
#
# services = guard:services/guard_service.py:foreground:sticky:foregroundServiceType=location|camera
#
# A true "cannot power off" shield requires either:
#   * device-owner provisioning + DevicePolicyManager lock-task mode, or
#   * an AccessibilityService that dismisses the global power dialog.
# Both are intentionally left as documented hooks in android_bridge.py.
# ---------------------------------------------------------------------------

[buildozer]

log_level = 2
warn_on_root = 1
