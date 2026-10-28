"""Range preflight and display names for the forthcoming clip workflow."""
import math
from pathlib import Path

from config import seconds, validate


def clip_name(source, suffix, start, end=None, number=None):
    if not math.isfinite(start) or start < 0:
        raise ValueError('Некорректное время начала')
    def time_label(value):
        if not math.isfinite(value) or value < 0:
            raise ValueError('Некорректное время отрывка')
        whole = int(value)
        return (f'{whole // 3600}{whole // 60 % 60:02d}{whole % 60:02d}'
                if whole >= 3600 else f'{whole // 60}{whole % 60:02d}')
    label = time_label(start)
    if end is not None:
        label += '_' + time_label(end)
    if number is not None:
        if number < 2:
            raise ValueError('Некорректный номер отрывка')
        label += '_' + str(number)
    return f'{Path(source).stem}_{label}{suffix}.mp4'


def validate_range(start, end, length, cfg, fps):
    validate(cfg)
    if not all(math.isfinite(x) for x in (start, end, length, fps)) or not (
        0 <= start < end <= length and fps > 0
    ):
        raise ValueError('Проверьте границы In и Out')
    for prefix in ('video', 'audio'):
        total = sum(seconds(cfg['fades'][prefix + '_' + edge], fps)
                    for edge in ('in', 'out'))
        if total > end - start + 1e-7:
            raise ValueError(f'{prefix}: сумма Fade In и Out больше длительности отрывка')
