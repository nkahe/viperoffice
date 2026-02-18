import uno
import unohelper
import builtins

from com.sun.star.awt import XKeyHandler


def _state():
    key = "_vibreoffice_python_state"
    state = getattr(builtins, key, None)
    if state is None:
        state = {
            "started": False,
            "enabled": False,
            "mode": "NORMAL",
            "key_handler": None,
        }
        setattr(builtins, key, state)
    return state


def _current_doc():
    try:
        return XSCRIPTCONTEXT.getDocument()
    except Exception:
        return None


def _current_controller():
    doc = _current_doc()
    if doc is None:
        return None
    try:
        return doc.getCurrentController()
    except Exception:
        return None


def _view_cursor():
    controller = _current_controller()
    if controller is None:
        return None
    try:
        return controller.getViewCursor()
    except Exception:
        return None


def _text_cursor_from_view():
    view = _view_cursor()
    if view is None:
        return None
    try:
        return view.getText().createTextCursorByRange(view)
    except Exception:
        return None


def _set_raw_status(text):
    controller = _current_controller()
    if controller is None:
        return
    try:
        controller.StatusIndicator.start(text, 0)
    except Exception:
        # Non-fatal for phase 0.
        pass


def _set_mode(mode_name):
    _state()["mode"] = mode_name
    _set_raw_status(mode_name)


def _restore_status():
    controller = _current_controller()
    if controller is None:
        return
    try:
        layout = controller.getFrame().LayoutManager
        layout.destroyElement("private:resource/statusbar/statusbar")
        layout.createElement("private:resource/statusbar/statusbar")
    except Exception:
        pass


def _show_normal_cursor():
    tc = _text_cursor_from_view()
    controller = _current_controller()
    if tc is None or controller is None:
        return
    try:
        tc.gotoRange(tc.getStart(), False)
        moved = tc.goRight(1, False)
        if moved:
            tc.goLeft(1, True)
        controller.select(tc)
    except Exception:
        pass


def _show_insert_cursor():
    tc = _text_cursor_from_view()
    controller = _current_controller()
    if tc is None or controller is None:
        return
    try:
        tc.gotoRange(tc.getStart(), False)
        controller.select(tc)
    except Exception:
        pass


def _goto_mode(mode_name):
    _set_mode(mode_name)
    if mode_name == "NORMAL":
        _show_normal_cursor()
    elif mode_name == "INSERT":
        _show_insert_cursor()


def _normalize_key_char(event):
    k = event.KeyChar
    if k is None:
        return ""

    if isinstance(k, str):
        return k

    # UNO key chars can arrive as non-str objects. Prefer textual form first.
    try:
        s = str(k)
        if len(s) == 1:
            return s
    except Exception:
        pass

    try:
        code = int(k)
    except Exception:
        code = -1

    if 0 <= code <= 255:
        return chr(code)

    # Fallback: some environments deliver letter keys as key codes.
    try:
        key_code = int(event.KeyCode)
        # com.sun.star.awt.Key.A..Z are typically 512..537.
        if 512 <= key_code <= 537:
            return chr(ord("a") + (key_code - 512))
        if 0 <= key_code <= 255:
            return chr(key_code)
    except Exception:
        pass

    return ""


def _move_view(key_char):
    view = _view_cursor()
    if view is None:
        return False
    try:
        if key_char == "h":
            return bool(view.goLeft(1, False))
        if key_char == "l":
            return bool(view.goRight(1, False))
        if key_char == "j":
            return bool(view.goDown(1, False))
        if key_char == "k":
            return bool(view.goUp(1, False))
    except Exception:
        return False
    return False


def _delete_char_under_cursor():
    tc = _text_cursor_from_view()
    if tc is None:
        return False
    try:
        tc.gotoRange(tc.getStart(), False)
        if not tc.goRight(1, True):
            return False
        tc.setString("")
        return True
    except Exception:
        return False


class KeyHandler(unohelper.Base, XKeyHandler):
    def keyPressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False

        key_char = _normalize_key_char(event)
        if len(key_char) == 1:
            key_char = key_char.lower()

        # ESC keycode in LibreOffice
        is_escape = (event.KeyCode == 1281)

        if state["mode"] == "INSERT":
            if is_escape:
                _goto_mode("NORMAL")
                return True
            return False

        # NORMAL mode: block input by default.
        if is_escape:
            _goto_mode("NORMAL")
            return True

        if key_char == "i":
            _goto_mode("INSERT")
            return True

        if key_char in ("h", "j", "k", "l"):
            _move_view(key_char)
            return True

        if key_char == "x":
            _delete_char_under_cursor()
            return True

        return True

    def keyReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        if state["mode"] == "NORMAL":
            _show_normal_cursor()
            return True
        if state["mode"] == "INSERT":
            _show_insert_cursor()
            return False
        return False

    def disposing(self, event):
        return None


def _attach_key_handler():
    state = _state()
    controller = _current_controller()
    if controller is None:
        return
    if state["key_handler"] is None:
        state["key_handler"] = KeyHandler()
    try:
        controller.removeKeyHandler(state["key_handler"])
    except Exception:
        pass
    controller.addKeyHandler(state["key_handler"])


def _detach_key_handler():
    state = _state()
    controller = _current_controller()
    if controller is None or state["key_handler"] is None:
        return
    try:
        controller.removeKeyHandler(state["key_handler"])
    except Exception:
        pass


def enableVibreoffice():
    state = _state()
    state["started"] = True
    state["enabled"] = True
    _attach_key_handler()
    _goto_mode("NORMAL")


def disableVibreoffice():
    state = _state()
    state["enabled"] = False
    _detach_key_handler()
    _restore_status()


def toggleVibreoffice():
    state = _state()
    if state["enabled"]:
        disableVibreoffice()
    else:
        enableVibreoffice()


g_exportedScripts = (toggleVibreoffice, enableVibreoffice, disableVibreoffice)
