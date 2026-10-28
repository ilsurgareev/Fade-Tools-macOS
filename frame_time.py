"""Elapsed minutes:seconds:frame labels on the source's actual frame grid.

These are elapsed-time labels, not SMPTE drop-frame or source timecode.
"""
import math


def format_frame_time(seconds, fps):
    if not math.isfinite(seconds) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Некорректное время или частота кадров')
    frame = max(0, round(seconds * fps))
    whole = math.floor(frame / fps + 1e-9)
    first = math.ceil(whole * fps - 1e-9)
    offset = frame - first
    minutes, sec = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    prefix = f'{hours}:{minutes:02d}:{sec:02d}' if hours else f'{minutes}:{sec:02d}'
    return f'{prefix}:{offset:0{max(2, len(str(math.ceil(fps)-1)))}d}'


def parse_frame_time(text, fps):
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Некорректная частота кадров')
    parts = text.strip().split(':')
    if len(parts) not in (2, 3, 4) or any(not part.isascii() or not part.isdigit() for part in parts):
        raise ValueError('Введите минуты:секунды:кадры')
    values = [int(part) for part in parts]
    frames = values.pop()
    second = values.pop()
    minute = values.pop() if values else 0
    hour = values.pop() if values else 0
    if second >= 60 or (len(parts) == 4 and minute >= 60):
        raise ValueError('Некорректное время')
    whole = hour * 3600 + minute * 60 + second
    first = math.ceil(whole * fps - 1e-9)
    end = math.ceil((whole + 1) * fps - 1e-9)
    if frames >= end - first:
        raise ValueError('Некорректный номер кадра')
    return (first + frames) / fps
