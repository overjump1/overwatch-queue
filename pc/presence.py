"""The vocabulary for Battle.net rich presence ("Competitive: In Queue"): what its text
means, and which processes are Battle.net's. Where the text comes from is `devmode`."""
from __future__ import annotations

import ctypes
import os

QUEUEING = "queueing"
IN_GAME = "inGame"
GAME_ENDING = "gameEnding"
MENUS = "menus"
PLAYING_OTHER = "playingOther"
ELSEWHERE = "elsewhere"
UNKNOWN = "unknown"

ACTIVITY_SUFFIXES = (("In Queue", QUEUEING), ("Game Ending", GAME_ENDING), ("In Game", IN_GAME))
STANDALONE = {"In Menus": MENUS, "Practice Range": PLAYING_OTHER, "Tutorial": PLAYING_OTHER}
MODE_WORDS = {
    "Quick Play": "quickPlay",
    "Competitive": "competitive",
    "Custom Game": "custom",
    "Arcade": "arcade",
    "Stadium": "stadium",
    "Mystery Heroes": "mysteryHeroes",
}
OVERWATCH_PROGRAM = "Pro"

AVAILABLE = os.name == "nt"


def parse(text):
    """`"Competitive: In Queue"` -> `(QUEUEING, "competitive")`. Unknown text is `(UNKNOWN, None)`."""
    text = (text or "").strip()
    if not text:
        return UNKNOWN, None
    if text in STANDALONE:
        return STANDALONE[text], None
    for suffix, state in ACTIVITY_SUFFIXES:
        if text.endswith(suffix):
            prefix = text[:-len(suffix)].rstrip().rstrip(":").rstrip()
            return state, MODE_WORDS.get(prefix)
    return UNKNOWN, None


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong), ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260),
    ]


def pids_named(name):
    """Every process id running `name` (lower-case). Cheap enough to run every read."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return frozenset()
    pids = set()
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        more = k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == name:
                pids.add(entry.th32ProcessID)
            more = k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snapshot)
    return frozenset(pids)
