import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config import DEFAULT, load, save, validate
from media import command, encoding, process, probe, run, tool, verify


@pytest.fixture
def cfg():
    c = copy.deepcopy(DEFAULT)
    c['codec'] = 'H.264'
    c['engine'] = 'CPU'
    for k in c['fades']:
        c['fades'][k].update(value=0.3, unit='seconds')
    c['fades']['video_in'].update(value=10, unit='frames')
    c['fades']['audio_out'].update(value=5, unit='frames')
    return c


@pytest.fixture(scope='session')
def sample(tmp_path_factory):
    root = tmp_path_factory.mktemp('media')
    src = root / 'Проверка с пробелами.mov'
    run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
         'color=c=white:s=320x180:r=30000/1001:d=3', '-f', 'lavfi', '-i',
         'sine=frequency=440:sample_rate=48000:duration=3.003', '-f', 'lavfi', '-i',
         'sine=frequency=880:sample_rate=48000:duration=1.8', '-filter_complex',
         '[1:a]pan=stereo|c0=c0|c1=c0[a1];[2:a]pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0[a2]',
         '-map', '0:v', '-map', '[a1]', '-map', '[a2]', '-c:v', 'libx264',
         '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709',
         '-color_primaries', 'bt709', '-color_trc', 'bt709', '-c:a', 'pcm_s24le',
         '-metadata:s:a:0', 'language=rus', '-metadata:s:a:1', 'language=eng',
         '-metadata', 'title=Fade acceptance', src])
    return src


def test_config_roundtrip(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path))
    save(cfg)
    assert load() == cfg
    assert not list(tmp_path.glob('*.tmp'))


def test_default_fades_use_frames():
    assert all(fade['unit'] == 'frames' for fade in DEFAULT['fades'].values())
    assert [DEFAULT['fades'][key]['value'] for key in ('video_in', 'video_out', 'audio_in', 'audio_out')] == [0, 40, 14, 53]
    assert DEFAULT['presets']['Стандартный']['fades'] == DEFAULT['fades']


def test_migrate_old_stock_fades_to_frame_default():
    old = copy.deepcopy(DEFAULT)
    old['fades'] = {
        key: {'enabled': True, 'value': 1.0, 'unit': 'seconds'}
        for key in old['fades']
    }
    from config import migrate
    migrated = migrate(old)
    assert migrated['fades'] == DEFAULT['fades']
    assert old['fades']['video_in']['unit'] == 'seconds'


def test_migrate_preserves_custom_fades():
    from config import migrate
    custom = copy.deepcopy(DEFAULT)
    custom['fades']['video_in'] = {'enabled': True, 'value': 12, 'unit': 'frames'}
    migrated = migrate(custom)
    assert migrated['fades']['video_in'] == custom['fades']['video_in']


def test_new_user_gets_defaults_and_user_changes_persist(tmp_path, monkeypatch):
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path))
    initial = load()
    assert initial['fades'] == DEFAULT['fades']
    changed = copy.deepcopy(initial)
    changed['fades']['video_out']['value'] = 27
    save(changed)
    assert load()['fades']['video_out']['value'] == 27


def test_migrate_without_writing(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path))
    cfg.update(version=1, codec='ProRes', engine='CPU')
    cfg.pop('video_bitrate_mbps')
    cfg['presets'] = {'Old preset': {k: copy.deepcopy(v) for k,v in cfg.items() if k != 'presets'}}
    path = tmp_path / 'settings.json'
    original = json.dumps(cfg).encode()
    path.write_bytes(original)
    upgraded = load()
    assert upgraded['codec'] == 'H.265' and upgraded['engine'] == DEFAULT['engine']
    assert upgraded['video_bitrate_mbps'] == 30 and upgraded['version'] == 2
    assert upgraded['presets']['Old preset']['video_bitrate_mbps'] == 30
    assert upgraded['fades'] == cfg['fades']
    assert path.read_bytes() == original


@pytest.mark.parametrize('bitrate', [0, 29, float('nan'), float('inf'), True, 1001])
def test_invalid_bitrate(cfg, bitrate):
    cfg['video_bitrate_mbps'] = bitrate
    with pytest.raises(ValueError):
        validate(cfg)


def test_reference_nvenc_settings():
    c = copy.deepcopy(DEFAULT)
    c['engine'] = 'NVENC'
    c['video_bitrate_mbps'] = 50
    args = encoding(c, {'pix_fmt': 'yuv420p10le'})
    expected = {'-c:v':'hevc_nvenc','-tune':'uhq','-preset':'p7','-rc':'vbr',
                '-b:v':'50000000','-multipass':'fullres','-rc-lookahead':'16',
                '-lookahead_level':'0','-aq-strength':'8','-profile:v':'main10'}
    for key,value in expected.items():
        assert args[args.index(key)+1] == value
    assert '-cq' not in args and '-crf' not in args


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf'), True])
def test_bad_durations(cfg, value):
    cfg['fades']['video_in']['value'] = value
    with pytest.raises(ValueError):
        validate(cfg)


@pytest.mark.parametrize('suffix', ['../bad', '\\bad', 'bad:thing', 'bad.', ' '])
def test_bad_suffix(cfg, suffix):
    cfg['suffix'] = suffix
    with pytest.raises(ValueError):
        validate(cfg)


def test_empty_suffix_roundtrip_including_preset(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path))
    cfg['suffix'] = ''
    cfg['presets'] = {'No suffix': {k: copy.deepcopy(v) for k, v in cfg.items() if k != 'presets'}}
    save(cfg)
    restored = load()
    assert restored['suffix'] == ''
    assert restored['presets']['No suffix']['suffix'] == ''


def test_empty_suffix_never_replaces_the_source_or_previous_output(tmp_path):
    source = tmp_path / 'source.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=25:d=1',
         '-c:v', 'libx264', source])
    cfg = copy.deepcopy(DEFAULT)
    cfg.update(engine='CPU', suffix='')
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    original = source.read_bytes()
    first = process(source, cfg)
    first_bytes = first.read_bytes()
    second = process(source, cfg)
    assert first.name == 'source_2.mp4'
    assert second.name == 'source_3.mp4'
    assert source.read_bytes() == original
    assert first.read_bytes() == first_bytes


def test_media_fades_and_no_overwrite(sample, cfg):
    original = hashlib.sha256(sample.read_bytes()).hexdigest()
    first = process(sample, cfg)
    first_bytes = first.read_bytes()
    second = process(sample, cfg)
    assert first != second
    assert first.read_bytes() == first_bytes
    assert hashlib.sha256(sample.read_bytes()).hexdigest() == original
    info = probe(first)
    assert len([s for s in info['streams'] if s['codec_type'] == 'audio']) == 2
    # Direct pixel measurements prove filters run, independent of generated command.
    pixels = run([tool('ffmpeg'), '-v', 'error', '-i', first, '-map', '0:v',
                  '-vf', 'scale=1:1,format=gray', '-f', 'rawvideo', '-'])
    assert pixels[0] < 5 and pixels[len(pixels)//2] > 240 and pixels[-1] < 5
    import array
    audio = array.array('f', run([tool('ffmpeg'), '-v', 'error', '-i', first,
                                '-map', '0:a:0', '-c:a', 'pcm_f32le', '-f', 'f32le', '-']))
    def rms(part):
        return (sum(x*x for x in part) / len(part)) ** 0.5
    assert rms(audio[:960]) < rms(audio[48000:48960]) * 0.1
    assert rms(audio[-960:]) < rms(audio[48000:48960]) * 0.1


@pytest.mark.parametrize('codec,engine,ten', [('H.264', 'CPU', False), ('H.265', 'CPU', True),
    ('ProRes', 'CPU', True), ('DNxHR', 'CPU', True), ('H.264', 'NVENC', False),
    ('H.265', 'NVENC', True), ('H.264', 'NVENC', True)])
def test_codecs(sample, cfg, codec, engine, ten, tmp_path):
    if sys.platform == 'darwin' and engine == 'NVENC':
        pytest.skip('NVIDIA NVENC is unavailable on macOS')
    src = sample
    if ten:
        src = tmp_path / 'ten.mov'
        run([tool('ffmpeg'), '-v', 'error', '-i', sample, '-map', '0:v', '-map', '0:a',
             '-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le',
             '-c:a', 'copy', '-color_range', 'tv', '-colorspace', 'bt709',
             '-color_trc', 'bt709', '-color_primaries', 'bt709', src])
    cfg.update(codec=codec, engine=engine, output_dir=str(tmp_path))
    out = process(src, cfg)
    v = next(s for s in probe(out)['streams'] if s['codec_type'] == 'video')
    assert v['codec_name'] == {'H.264': 'h264', 'H.265': 'hevc', 'ProRes': 'prores', 'DNxHR': 'dnxhd'}[codec]
    if ten:
        assert '10' in v['pix_fmt'] or int(v.get('bits_per_raw_sample', 0)) >= 10
    if codec == 'H.265':
        assert out.suffix == '.mp4' and v['codec_tag_string'] == 'hvc1'
        audio = [s for s in probe(out)['streams'] if s['codec_type'] == 'audio']
        assert [s['channels'] for s in audio] == [2, 6]
        assert all(s['codec_name'] == 'aac' for s in audio)


def test_excessive_fade_leaves_no_partial(sample, cfg, tmp_path):
    cfg['output_dir'] = str(tmp_path)
    cfg['fades']['audio_out']['value'] = 10000
    with pytest.raises(ValueError):
        process(sample, cfg)
    assert not list(tmp_path.iterdir())


def test_corrupt_and_continue(sample, cfg, monkeypatch, tmp_path):
    from droplet import main
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'appdata'))
    monkeypatch.setenv('FADE_TOOLS_TEST', '1')
    cfg['output_dir'] = str(tmp_path / 'out')
    save(cfg)
    bad = tmp_path / 'bad.mov'
    bad.write_bytes(b'not video')
    assert main([str(bad), str(sample)]) == 1
    assert len(list((tmp_path / 'out').glob('*.mov'))) == 1
    assert not list((tmp_path / 'out').glob('.fade-*'))


def test_validation_detects_dropped_audio(sample):
    before = probe(sample)
    after = copy.deepcopy(before)
    after['streams'].pop()
    with pytest.raises(ValueError):
        verify(before, after)


def test_validation_detects_dropped_frame(sample):
    before = probe(sample)
    after = copy.deepcopy(before)
    after['streams'][0]['nb_frames'] = '89'
    with pytest.raises(ValueError, match='число кадров'):
        verify(before, after)


def test_mkv_and_silent_video(sample, cfg, tmp_path):
    mkv = tmp_path / 'source.mkv'
    run([tool('ffmpeg'), '-v', 'error', '-i', sample, '-map', '0:v', '-map', '0:a',
         '-c:v', 'copy', '-c:a', 'flac', mkv])
    out = process(mkv, cfg)
    assert len([s for s in probe(out)['streams'] if s['codec_type'] == 'audio']) == 2
    silent = tmp_path / 'silent.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-i', sample, '-map', '0:v', '-c', 'copy', silent])
    result = process(silent, cfg)
    assert not [s for s in probe(result)['streams'] if s['codec_type'] == 'audio']


def test_hdr_rejected(sample, cfg, tmp_path):
    info = probe(sample)
    info['streams'][0]['color_transfer'] = 'smpte2084'
    with pytest.raises(ValueError, match='HDR'):
        command(sample, tmp_path / 'out.mov', cfg, info)


def test_gui_close_persists(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    app = QApplication.instance() or QApplication([])
    w = Settings()
    w.show()
    w.rows['video_in'][2].setCurrentIndex(w.rows['video_in'][2].findData('seconds'))
    w.rows['video_in'][1].setValue(0.72)
    w.rows['audio_out'][2].setCurrentIndex(1)
    w.rows['audio_out'][1].setValue(8)
    w.bitrate.setValue(50)
    w.suffix.setText('')
    w.close()
    app.processEvents()
    assert not w.isVisible()
    assert load()['fades']['video_in']['value'] == 0.72
    assert load()['video_bitrate_mbps'] == 50
    assert load()['suffix'] == ''
    assert load()['fades']['audio_out'] == {'enabled': True, 'value': 8, 'unit': 'frames'}
