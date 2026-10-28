"""Linear live-preview envelopes, in clip-local seconds (not an encoder)."""
from config import seconds


def gain(cfg, prefix, time, length, fps):
    if time < 0 or time >= length:
        return 0.0
    value = 1.0
    for edge in ('in', 'out'):
        fade = cfg['fades'][prefix + '_' + edge]
        d = seconds(fade, fps)
        if not d:
            continue
        t = time
        end = length - (1 / fps if prefix == 'video' else 0)
        if prefix == 'video' and fade['unit'] == 'frames':
            # The exporter ends frame-based fade out on the final image.
            t = round(time * fps) / fps
        factor = t / d if edge == 'in' else (end - t) / d
        value *= max(0.0, min(1.0, factor))
    return value
