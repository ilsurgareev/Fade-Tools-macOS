"""Live mode integration. No rendering is permitted until explicit queue submission."""
import time
from pathlib import Path


def test_live_playback_never_starts_encoder(sample, tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path/'settings'))
    from PySide6.QtWidgets import QApplication, QMessageBox
    from settings_app import Settings
    import clip_window
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, message: (_ for _ in ()).throw(RuntimeError(message)))
    calls = []
    monkeypatch.setattr(clip_window, 'prepare', lambda *a, **k: calls.append(a))
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    for _, value, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        value.setValue(.2)
    settings.open_clips()
    clips = settings.clip_window
    clips.show()
    clips.open_file(str(sample))
    clips.in_spin().setValue(.5005)
    clips.out.setValue(1.5015)
    clips.play_preview()
    assert clips.live_mode
    assert clips.queue.isEnabled()
    deadline = time.monotonic()+4
    gains = []
    while time.monotonic() < deadline:
        app.processEvents()
        gains.append((clips.video.item.opacity(), clips.audio.volume()))
        time.sleep(.01)
    assert not calls
    assert clips.active == 0 and not clips.timer.isActive()
    assert max(v for v,a in gains) > .9
    assert min(v for v,a in gains) < .1
    assert max(a for v,a in gains) > .9
    assert min(a for v,a in gains) < .1
    clips.seek(700)
    clips.refresh_live_video()
    settings.rows['audio_in'][2].setCurrentIndex(settings.rows['audio_in'][2].findData('seconds'))
    settings.rows['audio_in'][1].setValue(.4)
    assert clips.live_mode and clips.live_snapshot[3]['fades']['audio_in']['value'] == .4
    clips.show_original()
    assert clips.audio.volume() == 1 and clips.video.item.opacity() == 1
    clips.close()
    settings.close()

from test_fade import sample

def test_queue_snapshots_without_prepared_preview(sample, tmp_path, monkeypatch):
    import tempfile
    from media import probe
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path/'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.engine.setCurrentText('CPU')
    settings.folder.setText(str(tmp_path))
    for _, value, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        value.setValue(.1)
    settings.open_clips()
    clips = settings.clip_window
    clips.auto_render.setChecked(True)
    clips.open_file(str(sample))
    clips.in_spin().setValue(0)
    clips.out.setValue(1.001)
    clips.enqueue()
    clips.in_spin().setValue(1.001)
    clips.out.setValue(1.702)
    settings.rows['audio_in'][2].setCurrentIndex(settings.rows['audio_in'][2].findData('seconds'))
    settings.rows['audio_in'][1].setValue(.2)
    clips.enqueue()
    deadline = time.monotonic()+30
    while clips.active and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.01)
    assert clips.active == 0
    assert all('готово:' in clips.jobs.item(i).text() for i in range(2))
    files = [Path(clips.jobs.item(i).text().split(' · готово: ',1)[1]) for i in range(2)]
    assert clips.progress.isHidden()
    assert all(p.is_file() for p in files)
    assert files[0] != files[1]
    assert files[0].name.endswith('_000.mp4')
    assert files[1].name.endswith('_001.mp4')
    videos = [next(s for s in probe(p)['streams'] if s['codec_type']=='video') for p in files]
    assert [int(v['nb_frames']) for v in videos] == [30,21]
    clips.close()
    settings.close()


def test_failed_publication_never_shows_100(sample,tmp_path,monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME',str(tmp_path/'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    import clip_window
    def prepared(*args,**kwargs):
        token=kwargs['cancellation'];token.phase('checked',.95,.99);token.fraction=1
        args[4].write_bytes(b'candidate')
        return args[4]
    def failed(*args):
        raise PermissionError('Cannot publish')
    monkeypatch.setattr(clip_window,'prepare',prepared)
    monkeypatch.setattr(clip_window,'publish',failed)
    app=QApplication.instance() or QApplication([])
    settings=Settings();settings.open_clips();clips=settings.clip_window
    clips.auto_render.setChecked(True)
    clips.open_file(str(sample));clips.enqueue()
    deadline=time.monotonic()+3
    while clips.active and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert clips.active==0
    assert clips.progress.value()<1000
    assert 'ошибка' in clips.processing.text()
    assert not clips.open_result.isEnabled()
    clips.close();settings.close()
