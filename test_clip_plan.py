import copy
from pathlib import Path

import pytest

from config import DEFAULT
from clip_plan import clip_name, validate_range


def test_clip_name_uses_elapsed_minutes_and_seconds():
    assert clip_name(Path('song.mov'), '_fade', 64) == 'song_104_fade.mp4'
    assert clip_name(Path('song.mov'), '_fade', 3604) == 'song_10004_fade.mp4'


def test_filename_uses_only_the_whole_start_second():
    assert clip_name('song.mov', '', 64) == 'song_104.mp4'
    assert clip_name('song.mov', '', 64.2002) == 'song_104.mp4'
    assert clip_name('song.mov', '', 64.4004) == 'song_104.mp4'
    assert clip_name('song.mov', '_fade', 0.5005) == 'song_000_fade.mp4'


def test_range_rejects_short_clip_and_invalid_boundaries():
    cfg = copy.deepcopy(DEFAULT)
    with pytest.raises(ValueError, match='Fade'):
        validate_range(0, 1, 10, cfg, 25)
    with pytest.raises(ValueError, match='границ'):
        validate_range(5, 4, 10, cfg, 25)
    with pytest.raises(ValueError, match='границ'):
        validate_range(5, 11, 10, cfg, 25)


def test_frame_based_fades_use_source_rate():
    cfg = copy.deepcopy(DEFAULT)
    for fade in cfg['fades'].values():
        fade.update(value=25, unit='frames')
    validate_range(0, 2, 10, cfg, 25)
    with pytest.raises(ValueError, match='Fade'):
        validate_range(0, 1.9, 10, cfg, 25)
