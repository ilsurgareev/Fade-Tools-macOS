import copy
import hashlib
from pathlib import Path

import pytest

from config import DEFAULT
from media import probe, run, tool
from clip_engine import (align_range_to_timestamps, prepare, publish, range_timecode,
                         visible_dimensions, matching_export, export_signature)
from test_fade import sample


def test_timecode_rollover_and_ambiguous_or_drop_frame_rejected():
    info = {'streams': [{'codec_type': 'video', 'tags': {'timecode': '23:59:59:24'}}]}
    assert range_timecode(info, 25, 1) == '00:00:00:00'
    info['streams'][0]['tags']['timecode'] = '01:00:00:00'
    assert range_timecode(info, 30000/1001, 30) == '01:00:01:00'
    info['streams'][0]['tags']['timecode'] = '01:00:00;00'
    with pytest.raises(ValueError, match='drop-frame'):
        range_timecode(info, 30000/1001, 30)
    info['streams'][0]['tags']['timecode'] = '01:00:00:00'
    info['streams'].append({'codec_type': 'data', 'codec_tag_string': 'tmcd',
                            'tags': {'timecode': '02:00:00:00'}})
    with pytest.raises(ValueError, match='не совпадают'):
        range_timecode(info, 25, 0)


def test_visible_dimensions_reject_invalid_crop():
    video = {'width': 160, 'height': 90, 'side_data_list': [
        {'side_data_type': 'Frame Cropping', 'crop_right': 2}]}
    assert visible_dimensions(video) == (158, 90)
    video['side_data_list'][0]['crop_right'] = 1
    with pytest.raises(ValueError, match='чётным'):
        visible_dimensions(video)


def test_range_uses_nearest_verified_pts_not_nominal_fps():
    from array import array
    fps = 29.97
    timestamps = array('d', (frame / 30 for frame in range(151)))
    start, end = 30 / fps, 90 / fps
    first, last, aligned_start, aligned_end = align_range_to_timestamps(
        timestamps, start, end, 5)
    assert (first, last) == (30, 90)
    assert aligned_start == pytest.approx(1.0)
    assert aligned_end == pytest.approx(3.0)


def test_prepare_snaps_requested_times_to_actual_frame_timestamps(tmp_path, monkeypatch):
    import clip_engine
    source = tmp_path / 'true-30-fps.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30:d=5',
         '-an', '-c:v', 'libx264', source])
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.05, unit='seconds')
    original_inspect = clip_engine.inspect
    calls = []
    def slightly_wrong_rate(info):
        video, audio, fps = original_inspect(info)
        if not calls:
            calls.append(True)
            return video, audio, 29.97
        return video, audio, fps
    monkeypatch.setattr(clip_engine, 'inspect', slightly_wrong_rate)
    cache = {}
    target = tmp_path / 'aligned.mp4'
    prepare(source, 30/29.97, 90/29.97, cfg, target, analysis_cache=cache)
    assert cache['aligned_range'] == pytest.approx((1, 3))
    after = probe(target)
    output_video = next(s for s in after['streams'] if s['codec_type'] == 'video')
    assert int(output_video['nb_frames']) == 60


def test_timecode_and_container_crop_work_together(tmp_path):
    import struct
    source = tmp_path / 'base.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=25:d=2',
         '-c:v', 'libx264', '-timecode', '01:00:00:00', source])
    content = bytearray(source.read_bytes())
    def find_box(start, end, kind):
        cursor = start
        while cursor + 8 <= end:
            size = struct.unpack_from('>I', content, cursor)[0]
            assert size >= 8
            if content[cursor+4:cursor+8] == kind:
                return cursor, size
            cursor += size
        raise AssertionError(kind)
    moov, size = find_box(0, len(content), b'moov')
    assert moov + size == len(content)  # fixture has moov last, so media offsets do not move
    ancestors = [moov]
    parent, parent_size = moov, size
    for kind in (b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
        parent, parent_size = find_box(parent+8, parent+parent_size, kind)
        ancestors.append(parent)
    sample_entry = parent + 16  # stsd full-box fields and entry count
    sample_size = struct.unpack_from('>I', content, sample_entry)[0]
    ancestors.append(sample_entry)
    clap = struct.pack('>I4sIIIIiIiI', 40, b'clap', 158, 1, 90, 1, -1, 1, 0, 1)
    content[sample_entry+sample_size:sample_entry+sample_size] = clap
    for position in ancestors:
        old = struct.unpack_from('>I', content, position)[0]
        struct.pack_into('>I', content, position, old+len(clap))
    cropped = tmp_path / 'cropped.mp4'
    cropped.write_bytes(content)
    before = probe(cropped)
    video = next(s for s in before['streams'] if s['codec_type'] == 'video')
    assert visible_dimensions(video) == (158, 90)
    assert any(s.get('side_data_type') == 'Frame Cropping' for s in video['side_data_list'])
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    target = tmp_path / 'cropped-clip.mp4'
    prepare(cropped, .4, 1.4, cfg, target)
    after = probe(target)
    out = next(s for s in after['streams'] if s['codec_type'] == 'video')
    assert (out['width'], out['height']) == (158, 90)
    assert out['tags']['timecode'] == '01:00:00:10'


def test_verified_timeline_is_reused_and_invalidated_when_source_changes(sample, tmp_path, monkeypatch):
    import shutil
    import os
    import clip_engine
    source = tmp_path / 'copy.mov'
    shutil.copy2(sample, source)
    scans = []
    actual_scan = clip_engine.scan_timeline
    def scan(*args, **kwargs):
        scans.append(1)
        return actual_scan(*args, **kwargs)
    monkeypatch.setattr(clip_engine, 'scan_timeline', scan)
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    cache = {}
    prepare(source, 0, 1.001, cfg, tmp_path/'first.mp4', analysis_cache=cache)
    prepare(source, .5005, 1.5015, cfg, tmp_path/'second.mp4', analysis_cache=cache)
    assert len(scans) == 1
    state = source.stat()
    os.utime(source, ns=(state.st_atime_ns, state.st_mtime_ns+1_000_000))
    prepare(source, 0, 1.001, cfg, tmp_path/'third.mp4', analysis_cache=cache)
    assert len(scans) == 2


@pytest.mark.parametrize('suffix', ['_fade', ''])
def test_clip_preview_and_publication(sample, tmp_path, suffix):
    cfg = copy.deepcopy(DEFAULT)
    cfg.update(engine='CPU', output_dir=str(tmp_path))
    cfg['suffix'] = suffix
    for fade in cfg['fades'].values():
        fade.update(value=.2, unit='seconds')
    before = hashlib.sha256(sample.read_bytes()).digest()
    candidate = tmp_path / '.preview.mp4'
    prepare(sample, 0.5005, 1.5015, cfg, candidate)
    info = probe(candidate)
    assert len([s for s in info['streams'] if s['codec_type'] == 'audio']) == 2
    pixels = run([tool('ffmpeg'), '-v', 'error', '-i', candidate,
                  '-vf', 'scale=1:1,format=gray', '-f', 'rawvideo', '-'])
    assert len(pixels) == 30
    assert pixels[0] < 10 and pixels[15] > 235 and pixels[-1] < 15
    import array
    for stream in (0, 1):
        samples = array.array('f', run([tool('ffmpeg'), '-v', 'error', '-i', candidate,
            '-map', f'0:a:{stream}', '-ac', '1', '-f', 'f32le', '-']))
        def rms(values):
            return (sum(x*x for x in values) / len(values)) ** .5
        middle = rms(samples[23000:24000])
        assert middle > 0.001
        assert rms(samples[:500]) < middle * .15
        assert rms(samples[-500:]) < middle * .15
    first = publish(candidate, sample, .5005, cfg)
    assert matching_export(first, sample, .5005, 1.5015)
    assert not matching_export(first, sample, .5005, 2.002)
    previous_inode = first.stat().st_ino
    assert publish(candidate, sample, .5005, cfg) == first
    assert first.stat().st_ino != previous_inode
    first.write_bytes(b'previous output')
    with pytest.raises((FileExistsError, RuntimeError, ValueError)):
        publish(candidate, sample, .5005, cfg)
    assert first.name.endswith(f'_000{suffix}.mp4')
    assert first.read_bytes() == b'previous output'
    first.unlink()
    second = publish(candidate, sample, .5005, cfg)
    assert second == first
    assert second.read_bytes() == candidate.read_bytes()
    assert hashlib.sha256(sample.read_bytes()).digest() == before


def test_short_and_incomplete_audio_rejected(sample, tmp_path):
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    with pytest.raises(ValueError, match='Fade'):
        prepare(sample, 0, 1, cfg, tmp_path / 'short.mp4')
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    with pytest.raises(ValueError, match='Аудиодорожка'):
        prepare(sample, 0, 2.002, cfg, tmp_path / 'missing-audio.mp4')
    assert not list(tmp_path.glob('*.mp4'))


def test_vfr_is_explicitly_rejected(tmp_path):
    source = tmp_path / 'vfr.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=25:d=2',
         '-vf', "setpts=if(gte(N\\,25)\\,PTS+0.1/TB\\,PTS)", '-fps_mode', 'vfr',
         '-c:v', 'libx264', source])
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    with pytest.raises(ValueError, match='частота кадров'):
        prepare(source, 0, 1, cfg, tmp_path / 'out.mp4')


@pytest.mark.parametrize('rate,start,end,expected,frames', [
    ('25', .4, 1.4, '01:00:00:10', 25),
    ('30000/1001', .5005, 1.5015, '01:00:00:15', 30),
    ('24', .5, 1.5, '01:00:00:12', 24),
    ('50', .4, 1.4, '01:00:00:20', 50),
    ('60', .5, 1.5, '01:00:00:30', 60),
])
def test_timecode_is_not_silently_dropped(tmp_path, rate, start, end, expected, frames):
    source = tmp_path / 'timecode.mov'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=s=160x90:r={rate}:d=2',
         '-c:v', 'libx264', '-timecode', '01:00:00:00', source])
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    original = hashlib.sha256(source.read_bytes()).digest()
    target = tmp_path / 'out.mp4'
    prepare(source, start, end, cfg, target)
    result = probe(target)
    video = next(s for s in result['streams'] if s['codec_type'] == 'video')
    data = [s for s in result['streams'] if s['codec_type'] == 'data']
    assert video['tags']['timecode'] == expected
    assert len(data) == 1 and data[0]['codec_tag_string'] == 'tmcd'
    assert data[0]['tags']['timecode'] == expected
    assert int(video['nb_frames']) == frames
    assert original == hashlib.sha256(source.read_bytes()).digest()


def test_timecode_readback_failure_never_leaves_a_candidate(tmp_path, monkeypatch):
    import clip_engine
    source = tmp_path/'source.mov'
    target = tmp_path/'candidate.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=25:d=2',
         '-c:v', 'libx264', '-timecode', '01:00:00:00', source])
    actual_probe = clip_engine.probe
    def missing_clock(path, *args, **kwargs):
        info = actual_probe(path, *args, **kwargs)
        if Path(path) == target:
            info['streams'] = [s for s in info['streams'] if s.get('codec_type') != 'data']
            for stream in info['streams']:
                stream.get('tags', {}).pop('timecode', None)
        return info
    monkeypatch.setattr(clip_engine, 'probe', missing_clock)
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    with pytest.raises(ValueError, match='timecode отрывка'):
        prepare(source, .4, 1.4, cfg, target)
    assert not target.exists()


def test_selected_range_matches_source_frames(tmp_path):
    source = tmp_path / 'moving.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=25:d=2',
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', source])
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    candidate = tmp_path / 'candidate.mp4'
    prepare(source, .4, 1.4, cfg, candidate)
    def frame(path, index):
        return run([tool('ffmpeg'), '-v', 'error', '-i', path, '-vf',
                    f'select=eq(n\\,{index}),scale=64:36,format=rgb24',
                    '-frames:v', '1', '-f', 'rawvideo', '-'])
    output = frame(candidate, 12)
    actual = frame(source, 22)
    wrong = frame(source, 10)
    def distance(a, b):
        return sum(abs(x-y) for x, y in zip(a, b)) / len(a)
    assert len(output) == len(actual) == 64*36*3
    assert distance(output, actual) < 8
    assert distance(output, actual) < distance(output, wrong) / 2


def test_selected_audio_range_uses_source_timestamps(tmp_path):
    source = tmp_path / 'tones.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=25:d=2',
         '-f', 'lavfi', '-i', 'aevalsrc=sin(2*PI*if(lt(t\\,1)\\,440\\,880)*t):s=48000:d=2',
         '-c:v', 'libx264', '-c:a', 'aac', source])
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    candidate = tmp_path / 'audio-clip.mp4'
    prepare(source, .4, 1.4, cfg, candidate)
    import array
    data = array.array('f', run([tool('ffmpeg'), '-v', 'error', '-i', candidate,
                                 '-map', '0:a:0', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-']))
    def crossings(start):
        values = data[int(start*48000):int((start+.1)*48000)]
        return sum(a <= 0 < b for a, b in zip(values, values[1:]))
    assert 42 <= crossings(.3) <= 46  # source 0.7 seconds: 440 Hz
    assert 86 <= crossings(.8) <= 90  # source 1.2 seconds: 880 Hz


def test_ten_bit_depth_is_preserved(tmp_path):
    source = tmp_path / 'ten.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=25:d=2',
         '-pix_fmt', 'yuv420p10le', '-c:v', 'libx265', '-preset', 'ultrafast', source])
    cfg = copy.deepcopy(DEFAULT)
    cfg['engine'] = 'CPU'
    for fade in cfg['fades'].values():
        fade.update(value=.1, unit='seconds')
    candidate = tmp_path / 'ten-clip.mp4'
    prepare(source, .4, 1.4, cfg, candidate)
    video = next(s for s in probe(candidate)['streams'] if s['codec_type'] == 'video')
    assert '10' in video['pix_fmt'] or int(video.get('bits_per_raw_sample', 0)) == 10


def test_window_preview_and_queue(sample, tmp_path, monkeypatch):
    import time
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication, QMessageBox
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, message: (_ for _ in ()).throw(RuntimeError(message)))
    from settings_app import Settings
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.engine.setCurrentText('CPU')
    settings.folder.setText(str(tmp_path))
    for _, value, _ in settings.rows.values():
        value.setValue(.2)
    settings.open_clips()
    window = settings.clip_window
    window.auto_render.setChecked(True)
    window.open_file(str(sample))
    window.in_spin().setValue(.501)
    window.out.setValue(1.502)
    assert window.slider.selection == (501, 1502)
    assert abs(window.in_spin().value() * window.fps - round(window.in_spin().value() * window.fps)) < 1e-7
    assert abs(window.out.value() * window.fps - round(window.out.value() * window.fps)) < 1e-7
    assert window.jobs.isHidden()
    window.play_exact()
    assert window.resume_preview
    deadline = time.monotonic() + 30
    while not window.candidate and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert window.candidate, window.notice.text()
    assert window.preview_mode
    window.position_changed(250)
    playhead_time = (round(window.preview_start * 1000) + 250) / 1000
    nearest_playhead = min(window.frame_boundaries, key=lambda stamp: abs(stamp - playhead_time))
    assert window.slider.value() == int(nearest_playhead * 1000 + .5)
    first_preview = window.candidate.read_bytes()
    window.enqueue()
    window.in_spin().setValue(1.001)
    window.out.setValue(1.702)
    window.play_exact()
    while not window.candidate and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert window.candidate, window.notice.text()
    final_preview = window.candidate.read_bytes()
    window.enqueue()
    while window.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert 'готово:' in window.jobs.item(0).text(), window.jobs.item(0).text()
    assert 'готово:' in window.jobs.item(1).text(), window.jobs.item(1).text()
    assert len(list(tmp_path.glob('*_0*.mp4'))) == 2
    first_result = Path(window.jobs.item(0).text().split(' · готово: ', 1)[1])
    second_result = Path(window.jobs.item(1).text().split(' · готово: ', 1)[1])
    assert first_result != second_result
    assert first_result.read_bytes() == first_preview
    assert second_result.read_bytes() == final_preview
    window.close()
    settings.close()


def test_publish_never_replaces_source(tmp_path, monkeypatch):
    import clip_engine
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'original')
    candidate = tmp_path / 'candidate.mp4'
    candidate.write_bytes(b'ready')
    cfg = copy.deepcopy(DEFAULT)
    cfg['output_dir'] = str(tmp_path)
    monkeypatch.setattr(clip_engine, 'clip_name', lambda *args: source.name)
    with pytest.raises(ValueError, match='исходн'):
        publish(candidate, source, 0, cfg)
    assert source.read_bytes() == b'original'
    assert not list(tmp_path.glob('.fade-clip-*'))


def test_failed_replace_keeps_previous_output(tmp_path, monkeypatch):
    import clip_engine
    source = tmp_path / 'source.mp4'
    candidate = tmp_path / 'candidate.mp4'
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
         'testsrc2=s=160x90:r=25:d=1', '-c:v', 'libx264', source])
    run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
         'color=c=red:s=160x90:r=25:d=1', '-c:v', 'libx264',
         '-metadata', 'fade_tools_range=' + export_signature(source, 0, 1),
         '-movflags', '+use_metadata_tags', candidate])
    target = tmp_path / 'source_000.mp4'
    target.write_bytes(candidate.read_bytes())
    previous = target.read_bytes()
    original = source.read_bytes()
    cfg = copy.deepcopy(DEFAULT)
    cfg['output_dir'] = str(tmp_path)
    def fail(*args):
        raise PermissionError('locked')
    monkeypatch.setattr(clip_engine.os, 'replace', fail)
    with pytest.raises(PermissionError):
        publish(candidate, source, 0, cfg)
    assert target.read_bytes() == previous
    assert source.read_bytes() == original
    assert not list(tmp_path.glob('.fade-clip-*'))
