import functools
import threading
import time
from pathlib import Path

import rumps

from .control import Controller
from .i18n import tr

APP_NAME = tr("Я не робот")
ICON = Path(__file__).with_name("assets") / "menubar.png"
ALARM_ICON = Path(__file__).with_name("assets") / "menubar-alarm.png"
ALARM_SOUND_EVERY = 30  # seconds
UPDATE_CHECK_EVERY = 24 * 3600  # seconds
PERMISSIONS_EVERY = 60  # seconds
POPOVER_SIZE = (340, 470)


def install_edit_menu() -> None:
    """Gives the app an Edit menu: without one, Cmd+V/C/X/A do nothing in its windows (menu bar apps have no menu)."""
    from AppKit import NSApplication, NSMenu, NSMenuItem

    app = NSApplication.sharedApplication()
    main = NSMenu.alloc().init()
    app_menu = NSMenu.alloc().initWithTitle_(APP_NAME)
    app_menu.addItemWithTitle_action_keyEquivalent_(tr("О программе «{app}»", app=APP_NAME), "orderFrontStandardAboutPanel:", "")
    app_menu.addItem_(NSMenuItem.separatorItem())
    app_menu.addItemWithTitle_action_keyEquivalent_(tr("Скрыть окно"), "hide:", "h")
    app_menu.addItemWithTitle_action_keyEquivalent_(tr("Выйти из «{app}»", app=APP_NAME), "terminate:", "q")
    edit = NSMenu.alloc().initWithTitle_(tr("Правка"))
    for title, action, key in [("Отменить", "undo:", "z"), ("Повторить", "redo:", "Z"), ("Вырезать", "cut:", "x"),
                               ("Копировать", "copy:", "c"), ("Вставить", "paste:", "v"), ("Выбрать всё", "selectAll:", "a")]:
        edit.addItemWithTitle_action_keyEquivalent_(tr(title), action, key)
    window = NSMenu.alloc().initWithTitle_(tr("Окно"))
    window.addItemWithTitle_action_keyEquivalent_(tr("Закрыть"), "performClose:", "w")
    window.addItemWithTitle_action_keyEquivalent_(tr("Свернуть"), "performMiniaturize:", "m")
    for menu in (app_menu, edit, window):
        holder = NSMenuItem.alloc().init()
        holder.setSubmenu_(menu)
        main.addItem_(holder)
    app.setMainMenu_(main)


def _web_view(url: str, frame, transparent: bool = False):
    from Foundation import NSURL, NSURLRequest
    from WebKit import WKWebView, WKWebViewConfiguration

    web = WKWebView.alloc().initWithFrame_configuration_(frame, WKWebViewConfiguration.alloc().init())
    if transparent:
        web.setValue_forKey_(False, "drawsBackground")
    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
    return web


@functools.cache
def _classes():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSView
    from Foundation import NSObject

    class WindowDelegate(NSObject):
        def windowWillClose_(self, notification):
            # back to a pure menu bar app: no Dock icon once the window is gone
            NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    class DragStrip(NSView):
        """The page fills the title bar too; this strip over it keeps the window movable."""

        def mouseDown_(self, event):
            if event.clickCount() == 2:
                self.window().performZoom_(None)
            else:
                self.window().performWindowDragWithEvent_(event)

    class StatusTarget(NSObject):
        def initWithCallback_(self, callback):
            self = self.init()
            self.callback = callback
            return self

        def clicked_(self, sender):
            self.callback()

    return WindowDelegate, DragStrip, StatusTarget


class SettingsWindow:
    """The main window: the app's own page in a native window whose title bar the page draws itself.

    While it is open the app becomes a regular one (Dock icon, Cmd+Tab), otherwise the window is lost behind other
    apps' windows with no way to bring it back.
    """

    def __init__(self, url: str):
        self.url = url
        self.window = None
        self.web = None
        self.delegate = None

    def show(self, view: str = "") -> None:
        from AppKit import (NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSMakeRect,
                            NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
                            NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
                            NSWindowTitleHidden)

        app = NSApplication.sharedApplication()
        if self.window is None:
            delegate_cls, strip_cls, _ = _classes()
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
                     | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskFullSizeContentView)
            w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 980, 660), style, NSBackingStoreBuffered, False)
            w.setTitle_(APP_NAME)
            w.setTitleVisibility_(NSWindowTitleHidden)
            w.setTitlebarAppearsTransparent_(True)
            w.setReleasedWhenClosed_(False)
            w.setMinSize_((780, 540))
            w.center()
            w.setFrameAutosaveName_("YaNeRobotWindow")
            content = w.contentView()
            bounds = content.bounds()
            self.web = _web_view(self.url + (f"&open={view}" if view else ""), bounds)
            self.web.setAutoresizingMask_(2 | 16)  # width and height follow the window
            content.addSubview_(self.web)
            # leaves the traffic lights clickable: the strip starts to their right
            strip = strip_cls.alloc().initWithFrame_(NSMakeRect(80, bounds.size.height - 38, bounds.size.width - 80, 38))
            strip.setAutoresizingMask_(2 | 8)  # follows the width, stays at the top
            content.addSubview_(strip)
            self.delegate = delegate_cls.alloc().init()
            w.setDelegate_(self.delegate)
            self.window = w
        elif view:
            self.web.evaluateJavaScript_completionHandler_(f"show({view!r})", None)
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        app.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)


class MenuPanel:
    """The panel under the menu bar icon: the same page in its compact view, closed by any click outside."""

    def __init__(self, url: str):
        self.url = url
        self.popover = None
        self.height = POPOVER_SIZE[1]

    def toggle(self, button) -> None:
        from AppKit import NSMakeRect, NSMinYEdge, NSPopover, NSViewController

        if self.popover is None:
            vc = NSViewController.alloc().init()
            vc.setView_(_web_view(self.url, NSMakeRect(0, 0, *POPOVER_SIZE), transparent=True))
            p = NSPopover.alloc().init()
            p.setContentViewController_(vc)
            p.setContentSize_((POPOVER_SIZE[0], self.height))
            p.setBehavior_(1)  # transient: closes on a click elsewhere
            p.setAnimates_(True)
            self.popover = p
        if self.popover.isShown():
            self.popover.performClose_(None)
            return
        self.popover.contentViewController().view().evaluateJavaScript_completionHandler_("api('state')", None)
        self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSMinYEdge)
        self.popover.contentViewController().view().window().makeKeyWindow()

    def resize(self, height: int) -> None:
        self.height = max(200, min(height, 900))
        if self.popover is not None:
            self.popover.setContentSize_((POPOVER_SIZE[0], self.height))

    def close(self) -> None:
        if self.popover is not None and self.popover.isShown():
            self.popover.performClose_(None)


class MenuBarApp(rumps.App):
    def __init__(self, controller: Controller):
        # template image: macOS recolours it for light and dark menu bars like system icons
        super().__init__(APP_NAME, icon=str(ICON), template=True, quit_button=None)
        self.c = controller
        page = f"{controller.address}/ui?t={controller.ui_token}"
        self.window = SettingsWindow(page)
        self.panel = MenuPanel(page + "&view=pop")
        controller.hooks["open-window"] = lambda view: self._on_main(self.open_window, view)
        controller.hooks["quit"] = lambda _: self._on_main(self.quit, None)
        controller.hooks["panel-size"] = lambda h: self._on_main(self.panel.resize, h)
        self._started = False
        self._alarm_sounded = 0.0
        self._perm_checked = 0.0
        rumps.Timer(self.tick, 2).start()
        threading.Thread(target=self._check_human, daemon=True).start()
        threading.Thread(target=self._keep_companions, daemon=True).start()
        threading.Thread(target=self._check_updates, daemon=True).start()

    @staticmethod
    def _on_main(fn, arg) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn, arg)

    def _check_human(self) -> None:
        if self.c.cfg.human_api_key:
            self.c.check_human()

    def _check_updates(self) -> None:
        while True:
            self.c.check_update()
            time.sleep(UPDATE_CHECK_EVERY)

    def _keep_companions(self) -> None:
        while True:
            self.c.start_companions()
            time.sleep(60)

    def _take_over_status_item(self) -> None:
        """A click on the icon opens the panel instead of rumps' menu."""
        item = self._nsapp.nsstatusitem
        item.setMenu_(None)
        self._target = _classes()[2].alloc().initWithCallback_(self._icon_clicked)
        item.button().setTarget_(self._target)
        item.button().setAction_("clicked:")

    def _icon_clicked(self) -> None:
        self.c.quiet_alarm()
        self.show_alarm()
        self.panel.toggle(self._nsapp.nsstatusitem.button())

    def show_alarm(self) -> None:
        """Red icon and a sound repeated until the icon is clicked: a run of paid captchas usually means a broken layer."""
        alarm = bool(self.c.human_alarm())
        if alarm != getattr(self, "_alarm_shown", False):
            self._alarm_shown = alarm
            self.template = not alarm
            self.icon = str(ALARM_ICON if alarm else ICON)
        if alarm and time.monotonic() - self._alarm_sounded >= ALARM_SOUND_EVERY:
            from AppKit import NSSound

            self._alarm_sounded = time.monotonic()
            NSSound.soundNamed_("Sosumi").play()

    def tick(self, _=None) -> None:
        if not self._started:
            self._started = True
            install_edit_menu()
            self._take_over_status_item()
            if not self.c.cfg.keys and not self.c.cfg.human_api_key:
                self.open_window()
        self.notify_new_alerts()
        self.show_alarm()
        if time.monotonic() - self._perm_checked >= PERMISSIONS_EVERY:
            # on this thread the notification center answers; the window then shows what is off
            self._perm_checked = time.monotonic()
            self.c.refresh_permissions()
        job = self.c.bench
        self.title = f"{job['done']}/{job['total']}" if job.get("running") else None

    def notify_new_alerts(self) -> None:
        current = dict(self.c.alerts())
        seen = getattr(self, "_seen_alerts", set())
        for alert_id, text in current.items():
            if alert_id not in seen:
                rumps.notification(APP_NAME, "", text)
        self._seen_alerts = set(current)

    def open_window(self, view: str = "") -> None:
        self.panel.close()
        self.window.show(view or "")

    def quit(self, _) -> None:
        self.c.stop_server()
        rumps.quit_application()


def main() -> None:
    controller = Controller()
    if controller.starts_at_login():
        # the login item points at the executable; after a rename or a move it must follow the app
        controller.set_start_at_login(True)
    controller.start_server()
    MenuBarApp(controller).run()


if __name__ == "__main__":
    main()
