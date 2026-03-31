# flake8: noqa
from __future__ import annotations
from typing import TYPE_CHECKING, Final
import sys
import threading
import unohelper
from com.sun.star.document import XEventListener
from com.sun.star.awt import XMouseClickHandler

if TYPE_CHECKING:
    from core import (  # noqa: F401
        XSCRIPTCONTEXT,
        _get_controller,
        _goto_mode,
        _handle_exc,
        _is_forward_selection,
        _reset_count,
        _reset_pending_keys,
        _set_mode,
        _set_visual_anchor,
        _set_visual_selection,
        _show_cursor,
        _state,
        _update_statusline,
    )

    from viperoffice import (  # noqa: F401
        KeyHandler
    )

# This module includes non-editing functionality: initialization, enabling and
# disabling extension, handling controllers, listening events.

_HOST = globals().get("_HOST")
if _HOST is None:
    _HOST = sys.modules.get("viperoffice_host")
if _HOST is None:
    raise ImportError("viperoffice host module not registered")

# Retry limit when detaching key handlers to avoid stale-UNO handler buildup.
MAX_HANDLER_REMOVE_ATTEMPTS: Final[int] = 3

# Re-export host symbols so infra functions can access them as globals.
globals().update(
    { k: v for k, v in _HOST.__dict__.items() if not k.startswith("__") }
)

def enable_viper_office():
    """Enable ViperOffice"""
    state = _state()
    if not state["started"]:
        _initialize()
    state["enabled"] = True
    _activate_for_current_view()
    controller = _get_controller()
    if controller is not None:
        state["view_cursor"] = controller.getViewCursor()
    _set_mode("normal")
    _show_cursor("normal")


def disable_viper_office():
    """Disable ViperOffice"""
    state = _state()
    state["enabled"] = False
    _restore_status_all_views()
    _restore_lo_default_cursor_all_views()


def toggle_viper_office():
    """Toggle enabling of ViperOffice"""
    state = _state()
    if state["enabled"] is True:
        disable_viper_office()
    else:
        enable_viper_office()


def _initialize():
    state = _state()
    state["started"] = True
    # Detach any previously registered handler before creating a new one.
    _detach_key_handler_from_all_views()
    state["key_handler"] = KeyHandler()
    _attach_key_handler_to_all_views()
    _start_view_event_listener()
    enable_viper_office()


def _activate_for_current_view():
    state = _state()
    controller = _get_controller()
    if controller is None:
        return
    _state()["view_cursor"] = controller.getViewCursor()
    _update_statusline(controller)
    if state["mode"] == "normal":
        _show_normal_cursor_for_controller(controller)
    else:
        _show_insert_cursor_for_controller(controller)


def _restore_status_all_views():
    for controller in _iter_text_document_controllers():
        _restore_status_for_controller(controller)


def _restore_lo_default_cursor_all_views():
    for controller in _iter_text_document_controllers():
        _show_insert_cursor_for_controller(controller)


def _restore_status_for_controller(controller):
    if controller is None:
        return
    try:
        layout = controller.getFrame().LayoutManager
        layout.destroyElement("private:resource/statusbar/statusbar")
        layout.createElement("private:resource/statusbar/statusbar")
    except Exception as e:
        _handle_exc(err=e)
        pass


def _iter_text_document_controllers():
    desktop = _desktop()
    if desktop is None:
        return
    try:
        components = desktop.getComponents()
    except Exception as e:
        _handle_exc(err=e)
        return
    if components is None:
        return
    try:
        if not components.hasElements():
            return
        enum = components.createEnumeration()
        while enum.hasMoreElements():
            component = enum.nextElement()
            if not _is_text_document(component):
                continue
            try:
                controller = component.getCurrentController()
            except Exception as e:
                _handle_exc(err=e)
                controller = None
            if controller is not None:
                yield controller
    except Exception as e:
        _handle_exc(err=e)
        return


def _desktop():
    try:
        return XSCRIPTCONTEXT.getDesktop()
    except Exception as e:
        _handle_exc(err=e)
        return None


# If component is oducment not for example Calc sheet.
def _is_text_document(doc):
    if doc is None:
        return False
    try:
        return bool(doc.supportsService("com.sun.star.text.TextDocument"))
    except Exception as e:
        _handle_exc(err=e)
        return False


def _detach_key_handler_from_all_views():
    for controller in _iter_text_document_controllers():
        _detach_controller(controller)


def _detach_controller(controller):
    state = _state()
    if controller is None or state["key_handler"] is None:
        return
    for _ in range(MAX_HANDLER_REMOVE_ATTEMPTS):
        try:
            controller.removeKeyHandler(state["key_handler"])
        except Exception as e:
            _handle_exc(err=e)
            break
    listener = state.get("mouse_listener")
    controllers = state.get("mouse_listener_controllers")
    if listener is not None and controllers and id(controller) in controllers:
        try:
            controller.removeMouseClickHandler(listener)
        except Exception as e:
            _handle_exc(err=e)
            pass
        controllers.discard(id(controller))


def _attach_key_handler_to_all_views():
    controller_count = 0
    for controller in _iter_text_document_controllers():
        _attach_controller(controller)
        controller_count += 1
    return controller_count


def _attach_controller(controller):
    state = _state()
    if controller is None or state["key_handler"] is None:
        return
    try:
        controller.addKeyHandler(state["key_handler"])
    except Exception as e:
        _handle_exc(err=e)
        pass
    listener = state.get("mouse_listener")
    if listener is None:
        listener = MouseSelectionListener()
        state["mouse_listener"] = listener
    controllers = state.setdefault("mouse_listener_controllers", set())
    if id(controller) not in controllers:
        try:
            controller.addMouseClickHandler(listener)
            controllers.add(id(controller))
        except Exception as e:
            _handle_exc(err=e)
            pass


def _start_view_event_listener():
    _stop_view_event_listener()
    state = _state()
    broadcaster = _global_event_broadcaster()
    if broadcaster is None:
        return
    listener = ViewEventListener()
    try:
        broadcaster.addEventListener(listener)
        state["view_event_listener"] = listener
    except Exception as e:
        _handle_exc(err=e)
        state["view_event_listener"] = None


def _stop_view_event_listener():
    state = _state()
    broadcaster = _global_event_broadcaster()
    listener = state.get("view_event_listener")
    if broadcaster is not None and listener is not None:
        try:
            broadcaster.removeEventListener(listener)
        except Exception as e:
            _handle_exc(err=e)
            pass
    state["view_event_listener"] = None


def _global_event_broadcaster():
    state = _state()
    if state["global_event_broadcaster"] is not None:
        return state["global_event_broadcaster"]
    try:
        ctx = XSCRIPTCONTEXT.getComponentContext()
        broadcaster = ctx.getByName("/singletons/com.sun.star.frame.theGlobalEventBroadcaster")
        state["global_event_broadcaster"] = broadcaster
        return broadcaster
    except Exception as e:
        _handle_exc(err=e)
        return None


class MouseSelectionListener(unohelper.Base, XMouseClickHandler):
    """Switches to visual mode when user selects text with the mouse.

    XMouseClickHandler is added to the controller via addMouseClickHandler
    and receives mouse events from the document editing area.
    Returns False to not consume the event (pass through to LibreOffice).
    """

    # NOTE: It wouldn't be reliable way to get start of selection by setting it in
    # this function. This gets called before LO has moved the cursor to position
    # where click happened.
    def mousePressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        mode = _state()["mode"]
        if mode.startswith("visual"):
            _goto_mode("normal")
            _reset_count()
        return False

    def mouseReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        _reset_count()
        _reset_pending_keys()
        controller = _get_controller()
        if controller is not None:
            try:
                state["view_cursor"] = controller.getViewCursor()
            except Exception as e:
                _handle_exc(err=e)
                pass
        cursor = _state()["view_cursor"]
        if cursor is None:
            return False
        try:
            sel_len = len(cursor.getString())
        except Exception as e:
            _handle_exc(err=e)
            sel_len = 0

        if sel_len < 2:
            # No extended selection -> apply Normal mode cursor.
            #
            # Use a short delay so LibreOffice finishes placing its
            # own cursor before we override it (race condition otherwise).
            if _state()["mode"] != "insert":
                threading.Timer(0.05, _show_cursor, args=["normal"]).start()
            return False

        self._apply_visual_mode_when_mouse_selection(cursor)
        return False

    # Didn't manage to get MouseDragListener to work correctly so this is used.
    # This needs less boilerplate code so better in that regard.
    def _apply_visual_mode_when_mouse_selection(self, cursor):
        """Applies Visual mode when text is selected with mouse in any window."""
        if cursor is None:
            return
        sel_start = cursor.getStart()
        sel_end = cursor.getEnd()
        if _is_forward_selection(cursor):
            anchor = sel_start
            caret = sel_end
        else:
            anchor = sel_end
            caret = sel_start
        # Apply after LO finishes its own mouse-up cursor update.
        threading.Timer(0.02, self._finalize_mouse_selection, args=[anchor, caret]).start()

    def _finalize_mouse_selection(self, anchor, caret) -> None:
        """Finalizes applying visual selection after mouse release (post-LO cursor update)."""
        try:
            cursor = _state()["view_cursor"]
            if cursor is None:
                return
            _set_mode("visual")
            _set_visual_anchor(anchor)
            _set_visual_selection(cursor, anchor, caret)
        except Exception as e:
            _handle_exc(err=e)
            pass

    def disposing(self, event):
        return None


class ViewEventListener(unohelper.Base, XEventListener):
    def notifyEvent(self, event):
        state = _state()
        if not state["enabled"] or event is None:
            return
        source = getattr(event, "Source", None)
        if not _is_text_document(source):
            return
        event_name = getattr(event, "EventName", "")
        try:
            if source is None:
                controller = None
            else:
                controller = source.getCurrentController()
        except Exception as e:
            _handle_exc(err=e)
            controller = None
        if event_name == "OnFocus":
            # Do not reattach on every focus change: in Python UNO this can
            # accumulate duplicate callbacks for the same handler.
            if controller is not None:
                _state()["view_cursor"] = controller.getViewCursor()
            _reset_count()
            if state["mode"] == "insert":
                _reset_pending_keys()
                _update_statusline(controller)
                _show_insert_cursor_for_controller(controller)
            else:
                _goto_mode("normal")
                _update_statusline(controller)
                _show_normal_cursor_for_controller(controller)
        elif event_name == "OnViewCreated":
            _attach_controller(controller)

    def disposing(self, event):
        return None


def _show_normal_cursor_for_controller(controller):
    if controller is None:
        return
    try:
        cursor = controller.getViewCursor()
        textCursor = cursor.getText().createTextCursorByRange(cursor)
        textCursor.gotoRange(textCursor.getStart(), False)
        moved = textCursor.goRight(1, False)
        if moved:
            textCursor.goLeft(1, True)
        controller.select(textCursor)
    except Exception as e:
        _handle_exc(err=e)
        pass


def _show_insert_cursor_for_controller(controller):
    if controller is None:
        return
    try:
        cursor = controller.getViewCursor()
        textCursor = cursor.getText().createTextCursorByRange(cursor)
        textCursor.gotoRange(textCursor.getStart(), False)
        controller.select(textCursor)
    except Exception as e:
        _handle_exc(err=e)
        pass
