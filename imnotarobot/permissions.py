"""Whether macOS lets the app run its login item in the background and show notifications.

Both checks talk to system frameworks, which may be missing (off macOS) or refuse to speak to a
process that is not the bundled app, so each returns True/False or None for "cannot tell". The
window only warns about a firm False; the functions are small so the tests can stand in for them.
"""

import sys
import time
from pathlib import Path

UN_ANSWER_TIMEOUT = 1.0  # seconds


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


def notifications_allowed() -> bool | None:
    """Whether macOS shows the app's notifications; rumps posts through the same permission.

    Never asked and style "none" both count as off, a denied or unknown state says so too.
    """
    if sys.platform != "darwin":
        return None
    try:
        from UserNotifications import (UNAuthorizationStatusAuthorized, UNAuthorizationStatusEphemeral,
                                       UNAuthorizationStatusProvisional, UNNotificationSettingDisabled,
                                       UNUserNotificationCenter)

        center = UNUserNotificationCenter.currentNotificationCenter()
        got: dict = {}

        def read(settings):
            try:
                got["status"] = int(settings.authorizationStatus())
                got["alert"] = int(settings.alertSetting())
            except Exception:
                got["error"] = True

        center.getNotificationSettingsWithCompletionHandler_(read)
        # the answer arrives on the center's own queue, so plain waiting is enough
        deadline = time.monotonic() + UN_ANSWER_TIMEOUT
        while not got and time.monotonic() < deadline:
            time.sleep(0.05)
        if got.get("error") or "status" not in got:
            return None
        authorized = got["status"] in (UNAuthorizationStatusAuthorized, UNAuthorizationStatusProvisional,
                                       UNAuthorizationStatusEphemeral)
        return authorized and got["alert"] != UNNotificationSettingDisabled
    except Exception:
        return None
