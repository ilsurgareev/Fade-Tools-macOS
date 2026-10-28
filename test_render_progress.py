import copy
import threading
import time
import pytest
from clip_engine import Cancellation, PreviewCancelled, prepare
from config import DEFAULT
from media import tool
from test_fade import sample


def test_ffmpeg_progress_is_live_and_cancellable():
    token = Cancellation()
    token.stage = 'encode'
    fractions = []
    stopped = threading.Event()
    def watch():
        while not stopped.wait(.01):
            if token.fraction is not None:
                fractions.append(token.fraction)
    thread=threading.Thread(target=watch);thread.start()
    try:
        result=token.run_progress([tool('ffmpeg'),'-v','error','-re','-f','lavfi','-i',
            'testsrc2=s=64x64:r=25:d=1','-f','null','-'],1)
    finally:
        stopped.set();thread.join()
    assert int(result['frame']) == 25
    assert any(0 < f < .95 for f in fractions)
    assert token.fraction == 1
    token.stage='next'
    assert token.fraction is None and token.speed == ''
    cancelled=Cancellation()
    timer=threading.Timer(.3,cancelled.cancel);timer.start()
    try:
        with pytest.raises(PreviewCancelled):
            cancelled.run_progress([tool('ffmpeg'),'-v','error','-re','-f','lavfi','-i',
                'testsrc2=s=64x64:r=25:d=20','-f','null','-'],20)
    finally:timer.cancel()
    assert cancelled.process is None


def test_wrong_full_decode_framecount_never_leaves_candidate(sample,tmp_path,monkeypatch):
    original=Cancellation.run_progress
    def tamper(self,args,length):
        result=original(self,args,length)
        if 'null' in args:result['frame']='1'
        return result
    monkeypatch.setattr(Cancellation,'run_progress',tamper)
    cfg=copy.deepcopy(DEFAULT);cfg['engine']='CPU'
    for fade in cfg['fades'].values():fade.update(value=.1,unit='seconds')
    target=tmp_path/'candidate.mp4'
    with pytest.raises(ValueError,match='декодированных'):
        prepare(sample,0,1.001,cfg,target)
    assert not target.exists()


def test_overall_progress_never_resets_or_finishes_before_publication():
    token=Cancellation()
    seen=[]
    for stage,low,high in [('scan',.03,.65),('encode',.65,.94),('metadata',.94,.95),
                           ('decode',.95,.99),('publish',.99,.999)]:
        token.phase(stage,low,high)
        seen.append(token.overall_fraction)
        for fraction in (0,.3,.2,.9,1):
            token.fraction=fraction
            seen.append(token.overall_fraction)
    assert seen==sorted(seen)
    assert max(seen)<1
    token.published()
    assert token.overall_fraction==1
