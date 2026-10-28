"""Whether macOS lets the app run its login item in the background and show notifications.

Nothing here may block its caller: SMAppService answers one XPC call, the notification center
calls its completion handler on a system queue, so answers are stored whenever they arrive and
readers keep the last known values. Both frameworks are fine with a plain worker thread — where
these functions must run, never on the menu bar's main thread or the server's event loop. Off
macOS, or when the frameworks are missing, everything answers None; the functions are small so
the tests can stand in for them.
"""

import sys
import threading
import time
from pathlib import Path

ANSWER_WAIT = 1.0  # seconds the asking thread stays alive for the notification answer


def background_allowed(agent: Path) -> bool | None:
    """Whether macOS lets the LaunchAgent run in the background (the "Allow in the Background" tick).

    Needs approval counts as not allowed; anything macOS will not confirm counts as unknown.
    """
    if sys.platform != "darwin":
        return None
    try:
        from Foundation import NSURL
        from ServiceManagement import SMAppService, SMAppServiceStatusEnabled, SMAppServiceStatusRequiresApproval

        status = int(SMAppService.statusForLegacyURL_(NSURL.fileURLWithPath_(str(agent))))
        if status == SMAppServiceStatusEnabled:
            return True
        if status == SMAppServiceStatusRequiresApproval:
            return False
        return None
    except Exception:
        return None


def ask_notifications(store) -> None:
    """Asks the notification center without waiting; store(True/False/None) gets the answer.

    Never asked and style "none" both count as off, a denied or unknown state says so too. The
    answer normally arrives on the center's own queue; this keeps the asking thread's run loop
    alive for a moment anyway, in case this macOS delivers it there — so call it from a worker
    thread, not from one that must not block.
    """
    if sys.platform != "darwin":
        store(None)
        return
    try:
        from Foundation import NSDate, NSRunLoop
        from UserNotifications import (UNAuthorizationStatusAuthorized, UNAuthorizationStatusEphemeral,
                                       UNAuthorizationStatusProvisional, UNNotificationSettingDisabled,
                                       UNUserNotificationCenter)

        center = UNUserNotificationCenter.currentNotificationCenter()
        answered = threading.Event()

        def read(settings):
            try:
                status = int(settings.authorizationStatus())
                alert = int(settings.alertSetting())
                authorized = status in (UNAuthorizationStatusAuthorized, UNAuthorizationStatusProvisional,
                                        UNAuthorizationStatusEphemeral)
                store(authorized and alert != UNNotificationSettingDisabled)
            except Exception:
                store(None)
            finally:
                answered.set()

        center.getNotificationSettingsWithCompletionHandler_(read)
        deadline = time.monotonic() + ANSWER_WAIT
        while not answered.is_set() and time.monotonic() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    except Exception:
        store(None)
