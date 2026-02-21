from typing import Protocol


class XKeyHandler(Protocol):
    def keyPressed(self, event): ...
    def keyReleased(self, event): ...
    def disposing(self, event): ...


class KeyModifier:
    MOD1: int


class Key:
    ESCAPE: int
    LEFT: int
    RIGHT: int
    UP: int
    DOWN: int
    HOME: int
    END: int
    DELETE: int
