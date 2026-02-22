import builtins
import datetime
import unohelper
from typing import Any

from com.sun.star.awt import XKeyHandler
from com.sun.star.awt import KeyModifier
from com.sun.star.awt import Key
from com.sun.star.awt import Rectangle
from com.sun.star.document import XEventListener


# ------------
# Global state
# ------------

# Provided by LibreOffice's Python macro runtime.
if "XSCRIPTCONTEXT" not in globals():
    XSCRIPTCONTEXT: Any = None

DEBUG = False
MAX_HANDLER_REMOVE_ATTEMPTS = 5


def _state():
    key = "_vipereoffice_state"
    state = getattr(builtins, key, None)
    if state is None:
        state = {
            # Has the extension been started. Will only be set to true.
            "started": False,
            "enabled": False,
            # Current vi input mode. Can be NORMAL or INSERT.
            "mode": "NORMAL",
            "key_handler": None,
            # Python UNO may leave stale key-handler registrations attached even
            # after removeKeyHandler(); token guards ensure only the latest
            # generation can execute commands.
            "active_handler_token": 0,
            "view_event_listener": None,
            "global_event_broadcaster": None,
            # For debug
            "enable_calls": 0,
            "disable_calls": 0,
            "toggle_calls": 0,
            # One-shot guards for duplicate transition callbacks from stale
            # handlers (Python UNO lifecycle quirk).
            "swallow_once_insert_press": False,
        }
        setattr(builtins, key, state)
    return state


# ------------
# Editor
# ------------

# Editor - Functions to manipulate view and model (document).

def _dbg(msg):
    if not DEBUG:
        return
    try:
        ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S.%f")
        with open("/tmp/vibreoffice-debug.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


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


def _get_cursor():
    controller = _current_controller()
    if controller is None:
        return None
    try:
        return controller.getViewCursor()
    except Exception:
        return None


def _get_text_cursor():
    cursor = _get_cursor()
    if cursor is None:
        return None
    try:
        return cursor.getText().createTextCursorByRange(cursor)
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


def _show_normal_cursor():
    textCursor = _get_text_cursor()
    controller = _current_controller()
    if textCursor is None or controller is None:
        return
    try:
        textCursor.gotoRange(textCursor.getStart(), False)
        moved = textCursor.goRight(1, False)
        if moved:
            textCursor.goLeft(1, True)
        controller.select(textCursor)
    except Exception:
        pass


def _show_insert_cursor():
    textCursor = _get_text_cursor()
    controller = _current_controller()
    if textCursor is None or controller is None:
        return
    try:
        textCursor.gotoRange(textCursor.getStart(), False)
        controller.select(textCursor)
    except Exception:
        pass


def _show_insert_cursor_for_controller(controller):
    if controller is None:
        return
    try:
        cursor = controller.getViewCursor()
        textCursor = cursor.getText().createTextCursorByRange(cursor)
        textCursor.gotoRange(textCursor.getStart(), False)
        controller.select(textCursor)
    except Exception:
        pass


def _goto_mode(mode_name):
    _set_mode(mode_name)
    if mode_name == "NORMAL":
        _show_normal_cursor()
    elif mode_name == "INSERT":
        _show_insert_cursor()


def _move_charwise(cmd):
    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if cmd == "h":
            return bool(cursor.goLeft(1, False))
        if cmd == "l":
            return bool(cursor.goRight(1, False))
        if cmd == "j":
            return bool(cursor.goDown(1, False))
        if cmd == "k":
            return bool(cursor.goUp(1, False))
    except Exception:
        return False
    return False


def _move_linewise(cmd):
    cursor = _get_cursor()
    if cursor is None:
        return False

    try:
        if cmd == "0":
            return bool(cursor.gotoStartOfLine(False))

        if cmd == "$":
            old_pos = cursor.getPosition()
            cursor.gotoEndOfLine(False)
            new_pos = cursor.getPosition()

            old_y = getattr(old_pos, "Y", None)
            if callable(old_y):
                old_y = old_y()
            new_y = getattr(new_pos, "Y", None)
            if callable(new_y):
                new_y = new_y()

            # LibreOffice can place cursor at next line start; move left back
            # to previous line end unless this was an empty-line no-op.
            if cursor.isAtStartOfLine() and old_y != new_y:
                cursor.goLeft(1, False)
            return True

    except Exception:
        return False


def _pos_xy(pos):
    if pos is None:
        return (None, None)
    x = getattr(pos, "X", None)
    y = getattr(pos, "Y", None)
    if callable(x):
        x = x()
    if callable(y):
        y = y()
    return (x, y)


def _same_pos(a, b):
    return _pos_xy(a) == _pos_xy(b)


def _is_current_paragraph_empty(text_cursor):
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor)
        probe.gotoStartOfParagraph(False)
        probe.gotoEndOfParagraph(True)
        return len(probe.getString()) == 0
    except Exception:
        return False


def _sync_view_cursor_to_text_cursor(view_cursor, text_cursor, expand):
    edge = text_cursor.getEnd() if expand else text_cursor.getStart()
    view_cursor.gotoRange(edge, False)


def _goto_next_non_empty_paragraph(text_cursor, expand):
    moved = False
    while True:
        if not text_cursor.gotoNextParagraph(expand):
            break
        moved = True
        if not _is_current_paragraph_empty(text_cursor):
            break
    return moved


def _go_to_next_sentence(expand):
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False

    try:
        old_pos = cursor.getPosition()

        # From an empty line, jump directly to the next non-empty paragraph.
        if _is_current_paragraph_empty(text_cursor):
            moved = _goto_next_non_empty_paragraph(text_cursor, expand)
            if moved:
                _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
            return moved

        text_cursor.gotoNextSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)

        if _same_pos(old_pos, cursor.getPosition()):
            if text_cursor.isEndOfParagraph():
                if _is_current_paragraph_empty(text_cursor):
                    _goto_next_non_empty_paragraph(text_cursor, expand)
                else:
                    text_cursor.gotoNextParagraph(expand)
            else:
                text_cursor.goRight(1, expand)
                text_cursor.gotoNextSentence(expand)
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        return True
    except Exception:
        return False


def _is_at_sentence_start_heuristic(text_cursor):
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
        if probe.isStartOfParagraph():
            return True

        ch = ""
        while True:
            if not probe.goLeft(1, True):
                break
            ch = probe.getString()
            probe.collapseToStart()
            if ch not in (" ", "\t", "\"", "'", ")", "]"):
                break
        return ch in (".", "!", "?")
    except Exception:
        return False


def _go_to_previous_sentence(expand):
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False

    try:
        old_pos = cursor.getPosition()

        # From inside a sentence, first "(" should go to current sentence start.
        if not _is_at_sentence_start_heuristic(text_cursor):
            text_cursor.gotoStartOfSentence(expand)
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
            return True

        # Paragraph-boundary behavior matching VBS logic.
        if text_cursor.isStartOfParagraph():
            if _is_current_paragraph_empty(text_cursor):
                moved = text_cursor.gotoPreviousParagraph(expand)
                if not moved:
                    return False
                while _is_current_paragraph_empty(text_cursor):
                    if not text_cursor.gotoPreviousParagraph(expand):
                        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                        return True
                text_cursor.gotoEndOfParagraph(expand)
                if not text_cursor.isStartOfParagraph():
                    text_cursor.goLeft(1, expand)
                text_cursor.gotoStartOfSentence(expand)
                _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                return True
            else:
                if text_cursor.gotoPreviousParagraph(expand):
                    if _is_current_paragraph_empty(text_cursor):
                        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                        return True
                    text_cursor.gotoEndOfParagraph(expand)
                    if not text_cursor.isStartOfParagraph():
                        text_cursor.goLeft(1, expand)
                    text_cursor.gotoStartOfSentence(expand)
                    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                    return True

        text_cursor.gotoPreviousSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        if _same_pos(old_pos, cursor.getPosition()):
            if text_cursor.goLeft(1, expand):
                text_cursor.gotoPreviousSentence(expand)
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        return True
    except Exception:
        return False


def _delete_char_under_cursor():
    textCursor = _get_text_cursor()
    if textCursor is None:
        return False
    try:
        textCursor.gotoRange(textCursor.getStart(), False)
        if not textCursor.goRight(1, True):
            return False
        textCursor.setString("")
        return True
    except Exception:
        return False


def _leave_insert_to_normal():
    cursor = _get_cursor()
    if cursor is not None:
        try:
            if not cursor.isAtStartOfLine():
                _move_charwise("h")
        except Exception:
            pass
    _goto_mode("NORMAL")


def _switch_to_insert(state, key_char):
    # Some stale handlers may still receive this same insert-transition key
    # callback. Swallow one stale duplicate so the transition key does not get
    # inserted as text.
    state["swallow_once_insert_press"] = True
    if key_char is "a":
        try:
            textCursor = _get_text_cursor()
            if textCursor is not None and not textCursor.isEndOfParagraph():
                _move_charwise("l")
        except Exception:
            pass
    _goto_mode("INSERT")


def _undo(isUndo):
    doc = _current_doc()
    if doc is None:
        return False
    try:
        if isUndo:
            doc.getUndoManager().undo()
        else:
            doc.getUndoManager().redo()
        return True
    except Exception:
        # Non-fatal when no more undo actions exist.
        return False


# --------------
# Input handling
# --------------


def _normal_actions(state):
    return {
        "a": lambda: _switch_to_insert(state, "a"),
        "i": lambda: _switch_to_insert(state, "i"),
        "h": lambda: _move_charwise("h"),
        "j": lambda: _move_charwise("j"),
        "k": lambda: _move_charwise("k"),
        "l": lambda: _move_charwise("l"),
        ")": lambda: _go_to_next_sentence(False),
        "(": lambda: _go_to_previous_sentence(False),
        "u": lambda: _undo(True),
        "U": lambda: _undo(False),
        "x": _delete_char_under_cursor,
        "0": lambda: _move_linewise("0"),
        "$": lambda: _move_linewise("$"),
    }


class KeyHandler(unohelper.Base, XKeyHandler):
    def __init__(self, token):
        self._token = token

    def _is_active_instance(self):
        return self._token == _state().get("active_handler_token")

    def _consume_active_event(self, action=None):
        if action is not None:
            action()
        return True

    # Return False: event consumed, False: let pass through.
    def keyPressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False

        # Don't do anything if textCursor isn't working (as in annotations).
        textCursor = _get_text_cursor()
        if textCursor is None:
            return False

        if not self._is_active_instance():
            # Stale handlers can still be called by LO after lifecycle changes.
            # Swallow one duplicate transition callback if needed.
            if state["mode"] == "INSERT" and state["swallow_once_insert_press"]:
                state["swallow_once_insert_press"] = False
                return True
            if state["mode"] == "NORMAL" and (
                _is_navigation_key(event) or _is_function_key(event)
            ):
                return False
            return bool(state["mode"] == "NORMAL")

        key_code = _key_code(event)
        key_char = _normalize_key_char(event)
        mods = _event_modifiers(event)
        is_escape = (key_code == 1281)

        if state["mode"] == "INSERT":
            if is_escape:
                return self._consume_active_event(_leave_insert_to_normal)
            return False

        # ----- Non-insert mode -----

        r_code = int(getattr(Key, "R", 529))

        if _is_ctrl_shortcut_no_alt_meta(mods):
            if key_code == r_code:
                return self._consume_active_event(lambda: _undo(False))
            else:
                return False

        is_altgr_char = _is_altgr_char_event(event, key_char, key_code)
        # Pass modified shortcuts through, except AltGr-only char input in
        # NORMAL mode, which ViperOffice should keep and interpret.
        if _has_non_shift_modifier(event):
            if not (state["mode"] == "NORMAL" and is_altgr_char):
                return False

        normal_actions = _normal_actions(state)

        action = normal_actions.get(key_char)
        if action is not None:
            return self._consume_active_event(action)

        # NORMAL mode: block input by default.
        if _is_insert_key(event):
            return self._consume_active_event(lambda: _switch_to_insert(state, "i"))
        if _is_delete_key(event):
            return self._consume_active_event(_delete_char_under_cursor)
        if _is_navigation_key(event) or _is_function_key(event):
            return False
        if is_escape:
            return self._consume_active_event(lambda: _goto_mode("NORMAL"))
        return self._consume_active_event()


    # Return False: event consumed, False: let pass through.
    def keyReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False

        # Keep NORMAL cursor rendering for navigation releases, including
        # Ctrl+Home/Ctrl+End where Ctrl would otherwise short-circuit below.
        if state["mode"] == "NORMAL" and _is_navigation_key(event):
            _show_normal_cursor()
            return False
        if state["mode"] == "NORMAL" and _is_function_key(event):
            return False

        if state["mode"] == "NORMAL":
            _show_normal_cursor()
            return True

        return False

    def disposing(self, event):
        return None


def _msgbox(text, title="ViperOffice"):
    try:
        controller = _current_controller()
        if controller is None:
            return
        parent = controller.getFrame().getContainerWindow()
        toolkit = parent.getToolkit()
        try:
            # Legacy UNO signature used by some versions.
            box = toolkit.createMessageBox(
                parent,
                Rectangle(),
                "infobox",
                1,
                title,
                str(text),
            )
        except Exception:
            # Newer UNO signature used by some versions.
            box = toolkit.createMessageBox(
                parent,
                1,
                1,
                title,
                str(text),
            )
        box.execute()
    except Exception:
        pass


def _normalize_key_char(event):
    k = event.KeyChar
    key_code = _key_code(event)

    # NUM0..NUM9 can represent either digits or symbols depending on layout.
    # If KeyChar already carries a printable symbol (e.g. AltGr+4 -> "$"),
    # prefer it over key-code based reconstruction.
    if 256 <= key_code <= 265:
        if isinstance(k, str) and len(k) == 1 and ord(k) >= 32:
            return k
        shift_mask = getattr(KeyModifier, "SHIFT", 1)
        is_shift = bool(_event_modifiers(event) & shift_mask)
        if is_shift:
            return ")!@#$%^&*("[key_code - 256]
        return chr(ord("0") + (key_code - 256))

    if k is None:
        return ""

    if isinstance(k, str):
        return k

    # UNO key chars can arrive as non-str objects. Prefer textual form first.
    try:
        # Some runtimes expose UNO Char wrappers like "<Char instance $>".
        for attr in ("value", "Value", "char", "Char"):
            v = getattr(k, attr, None)
            if isinstance(v, str) and len(v) == 1:
                return v
        s = str(k)
        if len(s) == 1:
            return s
        prefix = "<Char instance "
        if s.startswith(prefix) and s.endswith(">"):
            inner = s[len(prefix):-1]
            if len(inner) == 1:
                return inner
    except Exception:
        pass

    try:
        code = int(k)
    except Exception:
        code = -1

    if 0 <= code <= 255:
        return chr(code)

    # Fallback: some environments deliver characters as key codes.
    try:
        key_code = int(event.KeyCode)
        # com.sun.star.awt.Key.A..Z are typically 512..537.
        if 512 <= key_code <= 537:
            # Respect Shift when falling back to key codes, otherwise "HJKLIX"
            # would be treated as lowercase NORMAL-mode commands.
            shift_mask = getattr(KeyModifier, "SHIFT", 1)
            is_shift = bool(_event_modifiers(event) & shift_mask)
            base = ord("A") if is_shift else ord("a")
            return chr(base + (key_code - 512))

        # Ignore NUL keycode (0); it is often a non-printable placeholder.
        if 1 <= key_code <= 255:
            return chr(key_code)

    except Exception:
        pass

    return ""


def _event_modifiers(event):
    try:
        return int(event.Modifiers)
    except Exception:
        return 0


def _has_non_shift_modifier(event):
    mods = _event_modifiers(event)
    return bool(mods & (KeyModifier.MOD1 | KeyModifier.MOD2 | KeyModifier.MOD3))


def _is_ctrl_shortcut_no_alt_meta(mods):
    ctrl = getattr(KeyModifier, "MOD1", 0)
    alt = getattr(KeyModifier, "MOD2", 0)
    meta = getattr(KeyModifier, "MOD3", 0)
    return bool(mods & ctrl) and not bool(mods & (alt | meta))


def _is_altgr_char_event(event, key_char, key_code):
    if not (isinstance(key_char, str) and len(key_char) == 1 and ord(key_char) >= 32):
        return False
    mods = _event_modifiers(event)
    mod2 = getattr(KeyModifier, "MOD2", 0)
    mod3 = getattr(KeyModifier, "MOD3", 0)
    # Treat AltGr as text-producing modified input. In this environment these
    # events arrive with key_code == 0 (e.g. AltGr+4 -> "$"), while normal
    # Ctrl/Alt shortcuts have concrete key codes.
    return bool(mods & mod2) and not bool(mods & mod3) and key_code == 0


def _key_code(event):
    try:
        return int(event.KeyCode)
    except Exception:
        return -1


def _is_navigation_key(event):
    # LibreOffice key codes: Home/End/Left/Right/Up/Down/PageUp/PageDown.
    return _key_code(event) in (1024, 1025, 1026, 1027, 1028, 1029, 1030, 1031)


def _is_insert_key(event):
    try:
        return _key_code(event) == int(getattr(Key, "INSERT"))
    except Exception:
        return False


def _is_delete_key(event):
    try:
        return _key_code(event) == int(getattr(Key, "DELETE"))
    except Exception:
        return False


def _is_function_key(event):
    key_code = _key_code(event)
    for i in range(1, 13):
        try:
            if key_code == int(getattr(Key, f"F{i}")):
                return True
        except Exception:
            continue
    return False


# ---------------
# Infra
# ---------------

# Non-editor functionality: initialization, enabling and disabling for all
# windows, listening events.

def _desktop():
    try:
        return XSCRIPTCONTEXT.getDesktop()
    except Exception:
        return None


# If component is oducment not for example Calc sheet.
def _is_text_document(doc):
    if doc is None:
        return False
    try:
        return bool(doc.supportsService("com.sun.star.text.TextDocument"))
    except Exception:
        return False


def _iter_text_document_controllers():
    desktop = _desktop()
    if desktop is None:
        return
    try:
        components = desktop.getComponents()
    except Exception:
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
            except Exception:
                controller = None
            if controller is not None:
                yield controller
    except Exception:
        return


def _global_event_broadcaster():
    state = _state()
    if state["global_event_broadcaster"] is not None:
        return state["global_event_broadcaster"]
    try:
        ctx = XSCRIPTCONTEXT.getComponentContext()
        broadcaster = ctx.getByName("/singletons/com.sun.star.frame.theGlobalEventBroadcaster")
        state["global_event_broadcaster"] = broadcaster
        return broadcaster
    except Exception:
        return None


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
        except Exception:
            break


def _attach_key_handler_to_all_views():
    count = 0
    for controller in _iter_text_document_controllers():
        _attach_controller(controller)
        count += 1
    return count


def _attach_controller(controller):
    state = _state()
    if controller is None or state["key_handler"] is None:
        return
    # LibreOffice can accumulate duplicate registrations of the same handler.
    # Drain old registrations so addKeyHandler keeps one effective handler.
    for _ in range(MAX_HANDLER_REMOVE_ATTEMPTS):
        try:
            controller.removeKeyHandler(state["key_handler"])
        except Exception:
            break
    try:
        controller.addKeyHandler(state["key_handler"])
    except Exception:
        pass


def _set_raw_status_for_controller(controller, text):
    if controller is None:
        return
    try:
        controller.StatusIndicator.start(text, 0)
    except Exception:
        pass


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
    except Exception:
        pass


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
            controller = source.getCurrentController()
        except Exception:
            controller = None
        if event_name == "OnFocus":
            # Do not reattach on every focus change: in Python UNO this can
            # accumulate duplicate callbacks for the same handler.
            _set_raw_status_for_controller(controller, state["mode"])
            if state["mode"] == "NORMAL":
                _show_normal_cursor_for_controller(controller)
            else:
                _show_insert_cursor_for_controller(controller)
        elif event_name == "OnViewCreated":
            _attach_controller(controller)

    def disposing(self, event):
        return None


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
    except Exception:
        state["view_event_listener"] = None


def _stop_view_event_listener():
    state = _state()
    broadcaster = _global_event_broadcaster()
    listener = state.get("view_event_listener")
    if broadcaster is not None and listener is not None:
        try:
            broadcaster.removeEventListener(listener)
        except Exception:
            pass
    state["view_event_listener"] = None


def _activate_for_current_view():
    state = _state()
    controller = _current_controller()
    if controller is None:
        return
    _set_raw_status_for_controller(controller, state["mode"])
    if state["mode"] == "NORMAL":
        _show_normal_cursor_for_controller(controller)
    else:
        _show_insert_cursor_for_controller(controller)


def _restore_status_for_controller(controller):
    if controller is None:
        return
    try:
        layout = controller.getFrame().LayoutManager
        layout.destroyElement("private:resource/statusbar/statusbar")
        layout.createElement("private:resource/statusbar/statusbar")
    except Exception:
        pass


def _restore_status_all_views():
    for controller in _iter_text_document_controllers():
        _restore_status_for_controller(controller)


def _restore_default_cursor_all_views():
    for controller in _iter_text_document_controllers():
        _show_insert_cursor_for_controller(controller)


def _initialize():
    state = _state()
    state["started"] = True
    # Detach while handler reference is still available.
    _detach_key_handler_from_all_views()
    _state()["key_handler"] = None
    _start_view_event_listener()
    _reinitialize()


def _reinitialize():
    _set_mode("NORMAL")
    _show_normal_cursor()


def _ensure_initialized():
    state = _state()
    if not state["started"]:
        _initialize()
    else:
        _reinitialize()


def _set_vibreoffice_enabled(enable_value):
    state = _state()
    if enable_value == state["enabled"]:
        return
    state["enabled"] = enable_value
    state["swallow_once_insert_press"] = False
    if state["enabled"]:
        state["active_handler_token"] += 1
        state["key_handler"] = KeyHandler(state["active_handler_token"])
        attached = _attach_key_handler_to_all_views()
        # Fallback only when enumeration finds no eligible text views.
        if attached == 0:
            _attach_controller(_current_controller())
        _activate_for_current_view()
    else:
        # Invalidate any stale attached handlers immediately, even if LO keeps
        # old registrations around internally.
        state["active_handler_token"] += 1
        _detach_key_handler_from_all_views()
        state["key_handler"] = None
        _restore_status_all_views()
        _restore_default_cursor_all_views()


def enable_viper_office():
    state = _state()
    state["enable_calls"] += 1
    _dbg(f"ENABLE call#{state['enable_calls']} state={id(state)}")
    _ensure_initialized()
    _set_vibreoffice_enabled(True)


def disable_viper_office():
    state = _state()
    state["disable_calls"] += 1
    _dbg(f"DISABLE call#{state['disable_calls']} state={id(state)}")
    _set_vibreoffice_enabled(False)


def toggle_viper_office():
    state = _state()
    state["toggle_calls"] += 1
    _dbg(f"TOGGLE call#{state['toggle_calls']} enabled_before={state['enabled']} state={id(state)}")
    _ensure_initialized()
    _set_vibreoffice_enabled(not state["enabled"])


g_exportedScripts = (toggle_viper_office, enable_viper_office, disable_viper_office)
