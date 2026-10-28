import copy
import pytest
from config import DEFAULT
from live_fade import gain


def test_live_envelopes_end_video_on_last_frame_audio_at_end():
    cfg = copy.deepcopy(DEFAULT)
    for fade in cfg['fades'].values():
        fade.update(value=1, unit='seconds')
    assert gain(cfg, 'video', 0, 4, 25) == 0
    assert gain(cfg, 'video', .5, 4, 25) == .5
    assert gain(cfg, 'video', 2, 4, 25) == 1
    assert gain(cfg, 'video', 3.96, 4, 25) == pytest.approx(0)
    assert gain(cfg, 'audio', 3.5, 4, 25) == .5
    assert gain(cfg, 'audio', -1, 4, 25) == 0
    cfg['fades']['video_out']['enabled'] = False
    assert gain(cfg, 'video', 3.96, 4, 25) == 1


def test_frame_fades_and_independent_audio():
    cfg = copy.deepcopy(DEFAULT)
    for f in cfg['fades'].values():
        f.update(value=10, unit='frames')
    assert gain(cfg, 'video', .2, 2, 25) == .5
    assert gain(cfg, 'video', 1.76, 2, 25) == pytest.approx(.5)
    cfg['fades']['audio_in']['value'] = 20
    assert gain(cfg, 'audio', .2, 2, 25) == .25

@pytest.mark.parametrize('unit,value', [('seconds', .4), ('frames', 10)])
def test_envelopes_against_actual_ffmpeg(unit, value):
    import array
    from media import pair_filters, run, tool
    cfg = copy.deepcopy(DEFAULT)
    for fade in cfg['fades'].values():
        fade.update(value=value, unit=unit)
    vf = ','.join(pair_filters(cfg, 'video', 25, 2, 0, True, 50))
    data = run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                'color=white:s=16x16:r=25:d=2', '-vf', vf+',scale=1:1,format=rgb24',
                '-f', 'rawvideo', '-'])
    assert len(data) == 150
    errors = [abs(data[n*3]/255 - gain(cfg, 'video', n/25, 2, 25)) for n in range(50)]
    assert max(errors) < .012
    af = ','.join(pair_filters(cfg, 'audio', 25, 2, 0))
    audio = array.array('f', run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
            'aevalsrc=0.5:s=48000:d=2', '-af', af, '-f', 'f32le', '-']))
    assert len(audio) == 96000
    assert max(abs(audio[n]*2-gain(cfg, 'audio', n/48000, 2, 25))
               for n in range(0,len(audio),480)) < .0001
