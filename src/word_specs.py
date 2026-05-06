# -------------------
# Word motion specs
# -------------------

FORWARD = "forward"
BACKWARD = "backward"
START = "start"
END = "end"

_WORD_MOTION_W = {
    "direction": FORWARD,
    "target": START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_B = {
    "direction": BACKWARD,
    "target": START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_BIG_B = {
    "direction": BACKWARD,
    "target": START,
    "big_word": True,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_E = {
    "direction": FORWARD,
    "target": END,
    "big_word": False,
    "cross_empty": False,
    "inclusive": True,
}
_WORD_MOTION_BIG_E = {
    "direction": FORWARD,
    "target": END,
    "big_word": True,
    "cross_empty": False,
    "inclusive": True,
}
_WORD_MOTION_GE = {
    "direction": BACKWARD,
    "target": END,
    "big_word": False,
    "cross_empty": True,
    "inclusive": True,
}
_WORD_MOTION_G_BIG_E = {
    "direction": BACKWARD,
    "target": END,
    "big_word": True,
    "cross_empty": True,
    "inclusive": True,
}
_WORD_MOTION_BIG_W = {
    "direction": FORWARD,
    "target": START,
    "big_word": True,
    "cross_empty": True,
    "inclusive": False,
}

# For text-object

_WORD_OBJECT_UNIT_FORWARD = {
    "direction": FORWARD,
    "target": END,
    "big_word": False,
    "cross_empty": True,
    "inclusive": True,
    "unit_mode": True,
}
_WORD_OBJECT_UNIT_BACKWARD = {
    "direction": BACKWARD,
    "target": START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
    "unit_mode": True,
}
_WORD_OBJECT_UNIT_BIG_FORWARD = {
    "direction": FORWARD,
    "target": END,
    "big_word": True,
    "cross_empty": True,
    "inclusive": True,
    "unit_mode": True,
}
_WORD_OBJECT_UNIT_BIG_BACKWARD = {
    "direction": BACKWARD,
    "target": START,
    "big_word": True,
    "cross_empty": True,
    "inclusive": False,
    "unit_mode": True,
}
