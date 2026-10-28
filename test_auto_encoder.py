import copy
from pathlib import Path
import pytest
from config import DEFAULT, migrate
from media import encoding, encode_with_fallback
from clip_engine import PreviewCancelled


def setup_encode(tmp_path):
    cfg = copy.deepcopy(DEFAULT)
    cfg.update(engine='Apple VideoToolbox', codec='H.265')
    video = {'pix_fmt': 'yuv420p10le'}
    target = tmp_path / 'candidate.mp4'
    args = ['ffmpeg', '-n', '-i', 'source.mov'] + encoding(cfg, video) + [target]
    return cfg, video, target, args


def test_hardware_success_does_not_retry(tmp_path):
    cfg, video, target, args = setup_encode(tmp_path)
    calls = []
    encode_with_fallback(args, cfg, video, target, lambda cmd: calls.append(cmd))
    assert calls == [args]
    assert args[args.index('-allow_sw')+1] == '0'
    assert args[args.index('-pix_fmt')+1] == 'p010le'


def test_hardware_failure_cleans_partial_and_retries_cpu(tmp_path):
    cfg, video, target, args = setup_encode(tmp_path)
    calls, statuses = [], []
    def execute(cmd):
        calls.append(cmd)
        if len(calls) == 1:
            target.write_bytes(b'partial')
            raise RuntimeError('VideoToolbox unavailable')
        assert not target.exists()
        assert 'libx265' in cmd and 'hevc_videotoolbox' not in cmd
        assert '-allow_sw' not in cmd and 'yuv420p10le' in cmd
        target.write_bytes(b'complete')
    encode_with_fallback(args, cfg, video, target, execute, statuses.append)
    assert len(calls) == 2 and 'CPU' in statuses[-1]
    assert target.read_bytes() == b'complete'
    assert cfg['engine'] == 'Apple VideoToolbox'


def test_cancel_never_retries(tmp_path):
    cfg, video, target, args = setup_encode(tmp_path)
    calls = []
    def execute(cmd):
        calls.append(cmd)
        raise PreviewCancelled('cancel')
    with pytest.raises(PreviewCancelled):
        encode_with_fallback(args, cfg, video, target, execute)
    assert len(calls) == 1


def test_old_cpu_settings_upgrade_to_automatic():
    old = copy.deepcopy(DEFAULT)
    old.pop('macos_engine_policy')
    old['engine'] = 'CPU'
    assert migrate(old)['engine'] == 'Apple VideoToolbox'
    assert old['engine'] == 'CPU'
