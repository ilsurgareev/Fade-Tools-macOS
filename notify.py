"""Native nonmodal balloon notifications; no Qt or console in the droplet."""
import ctypes
import os
import time
import subprocess
import sys
from ctypes import wintypes


def notify(title, message, error=False):
    if sys.platform == 'darwin' and not os.environ.get('FADE_TOOLS_TEST'):
        try:
            subprocess.run(['osascript', '-e',
                            'on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run',
                            title, message], check=False, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    if os.name != 'nt' or os.environ.get('FADE_TOOLS_TEST'):
        return
    class NID(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND),
                    ('uID', wintypes.UINT), ('uFlags', wintypes.UINT),
                    ('uCallbackMessage', wintypes.UINT), ('hIcon', wintypes.HICON),
                    ('szTip', wintypes.WCHAR * 128), ('dwState', wintypes.DWORD),
                    ('dwStateMask', wintypes.DWORD), ('szInfo', wintypes.WCHAR * 256),
                    ('uTimeout', wintypes.UINT), ('szInfoTitle', wintypes.WCHAR * 64),
                    ('dwInfoFlags', wintypes.DWORD), ('guidItem', ctypes.c_byte * 16),
                    ('hBalloonIcon', wintypes.HICON)]
    user = ctypes.windll.user32
    user.CreateWindowExW.restype = wintypes.HWND
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                    wintypes.HINSTANCE, wintypes.LPVOID]
    user.DestroyWindow.argtypes = [wintypes.HWND]
    user.LoadIconW.restype = wintypes.HICON
    user.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    hwnd = user.CreateWindowExW(0, 'STATIC', 'Fade Tools', 0, 0, 0, 0, 0, None, None, None, None)
    if not hwnd:
        return
    nid = NID()
    nid.cbSize = ctypes.sizeof(nid)
    nid.hWnd, nid.uID, nid.uFlags = hwnd, 1, 2 | 4 | 16
    nid.hIcon = user.LoadIconW(None, 32513 if error else 32516)
    nid.szTip = 'Fade Tools'
    nid.szInfoTitle, nid.szInfo = title[:63], message[:255]
    nid.dwInfoFlags, nid.uTimeout = (3 if error else 1), 6000
    shell = ctypes.windll.shell32
    shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NID)]
    try:
        shell.Shell_NotifyIconW(0, ctypes.byref(nid))
        end = time.monotonic() + 6
        msg = wintypes.MSG()
        while time.monotonic() < end:
            while user.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.1)
    finally:
        shell.Shell_NotifyIconW(2, ctypes.byref(nid))
        user.DestroyWindow(hwnd)

