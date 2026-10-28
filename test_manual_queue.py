import time
import tempfile
from pathlib import Path

from test_fade import sample


def test_manual_queue_waits_and_preserves_each_snapshot(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    import clip_window
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    clips = settings.clip_window
    clips.open_file(str(sample))
    for _, val, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    calls = []
    def prepare(source, start, end, cfg, target, **kwargs):
        calls.append((source, start, end, cfg['fades']['audio_in']['value'], cfg['output_dir']))
        target.write_bytes(b'verified candidate')
        return target
    def publish(candidate, source, start, cfg):
        result = Path(cfg['output_dir']) / ('result-' + str(len(calls)) + '.mp4')
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(candidate.read_bytes())
        return result
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    monkeypatch.setattr(clip_window, 'publish', publish)
    assert not clips.auto_render.isChecked()
    settings.folder.setText(str(tmp_path / 'first'))
    clips.out.setValue(1.001)
    clips.enqueue()
    settings.folder.setText(str(tmp_path / 'second'))
    settings.rows['audio_in'][2].setCurrentIndex(settings.rows['audio_in'][2].findData('seconds'))
    settings.rows['audio_in'][1].setValue(.2)
    clips.in_spin().setValue(1.001)
    clips.out.setValue(1.702)
    clips.enqueue()
    app.processEvents()
    assert len(clips.waiting_jobs) == 2 and not calls and clips.active == 0
    clips.start_queue()
    deadline = time.monotonic() + 5
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert len(calls) == 2 and clips.active == 0
    assert [round(x[3], 1) for x in calls] == [.1, .2]
    assert [x[4] for x in calls] == [str(tmp_path / 'first'), str(tmp_path / 'second')]
    assert all('готово:' in clips.jobs.item(i).text() for i in range(2))
    clips.close()
    settings.close()


def test_waiting_jobs_survive_close_and_can_be_removed(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication, QMessageBox
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.show()
    main.clips.open_file(str(sample))
    for _, val, unit in main.settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    main.clips.out.setValue(1.001)
    main.clips.enqueue()
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    assert main.close()
    restored = MainWindow()
    assert len(restored.clips.waiting_jobs) == 1
    restored.clips.jobs.setCurrentRow(0)
    restored.clips.remove_waiting_job()
    assert not restored.clips.waiting_jobs
    assert restored.close()
    again = MainWindow()
    assert not again.clips.waiting_jobs
    assert again.close()


def test_restored_job_renders_saved_snapshot(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    import clip_window
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.clips.open_file(str(sample))
    for _, val, unit in main.settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    main.settings.folder.setText(str(tmp_path / 'saved'))
    main.settings.rows['audio_in'][2].setCurrentIndex(main.settings.rows['audio_in'][2].findData('seconds'))
    main.settings.rows['audio_in'][1].setValue(.2)
    main.clips.out.setValue(1.001)
    main.clips.enqueue()
    assert main.close()
    restored = MainWindow()
    calls = []
    def prepare(source, start, end, cfg, target, **kwargs):
        calls.append((source, start, end, cfg['output_dir'], cfg['fades']['audio_in']['value']))
        target.write_bytes(b'verified')
        return target
    def publish(candidate, source, start, cfg, **kwargs):
        result = tmp_path / 'result.mp4'
        result.write_bytes(candidate.read_bytes())
        return result
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    monkeypatch.setattr(clip_window, 'publish', publish)
    restored.clips.start_queue()
    deadline = time.monotonic() + 5
    while restored.clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert len(calls) == 1 and calls[0][0] == sample, (restored.clips.queue_load_error, restored.clips.notice.text(), [restored.clips.jobs.item(i).text() for i in range(restored.clips.jobs.count())])
    assert calls[0][3:] == (str(tmp_path / 'saved'), .2)
    assert (tmp_path / 'result.mp4').read_bytes() == b'verified'
    assert restored.close()
    again = MainWindow()
    assert not again.clips.waiting_jobs
    assert again.close()


def test_corrupt_queue_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'clip_queue.json'
    path.write_text('{broken', encoding='utf-8')
    main = MainWindow()
    assert 'Не удалось восстановить очередь' in main.clips.notice.text()
    assert main.close()
    assert path.read_text(encoding='utf-8') == '{broken'


def test_clear_queue_removes_all_waiting_and_persists(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.clips.open_file(str(sample))
    for _, val, unit in main.settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    main.clips.out.setValue(1.001)
    main.clips.enqueue()
    main.clips.in_spin().setValue(1.001)
    main.clips.out.setValue(1.702)
    main.clips.enqueue()
    assert len(main.clips.waiting_jobs) == 2
    main.clips.clear_queue.click()
    assert not main.clips.waiting_jobs and main.clips.jobs.count() == 0
    assert not main.clips.clear_queue.isEnabled()
    assert main.close()
    restored = MainWindow()
    assert not restored.clips.waiting_jobs
    assert restored.close()


def test_clear_queue_save_failure_preserves_jobs(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.clips.open_file(str(sample))
    for _, val, unit in main.settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    main.clips.out.setValue(1.001)
    main.clips.enqueue()
    def fail():
        raise OSError('disk error')
    monkeypatch.setattr(main.clips, 'save_queue', fail)
    main.clips.clear_queue.click()
    assert len(main.clips.waiting_jobs) == 1 and main.clips.jobs.count() == 1
    assert 'disk error' in main.clips.notice.text()
    assert main.close()
    restored = MainWindow()
    assert len(restored.clips.waiting_jobs) == 1
    assert restored.close()


def test_clear_queue_keeps_active_render(sample, tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    import clip_window
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    clips = main.clips
    clips.open_file(str(sample))
    for _, val, unit in main.settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    clips.out.setValue(1.001)
    clips.enqueue()
    clips.in_spin().setValue(1.001)
    clips.out.setValue(1.702)
    clips.enqueue()
    entered, release = threading.Event(), threading.Event()
    def prepare(source, start, end, cfg, target, **kwargs):
        entered.set()
        assert release.wait(5)
        target.write_bytes(b'candidate')
        return target
    def publish(candidate, source, start, cfg, **kwargs):
        result = tmp_path / 'result.mp4'
        result.write_bytes(candidate.read_bytes())
        return result
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    monkeypatch.setattr(clip_window, 'publish', publish)
    clips.start_queue()
    assert entered.wait(5)
    clips.clear_queue.click()
    assert not clips.waiting_jobs and clips.active_queue_job
    assert not clips.active_queue_job[0][2].event.is_set()
    release.set()
    deadline = time.monotonic() + 5
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert (tmp_path / 'result.mp4').read_bytes() == b'candidate'
    assert main.close()


def test_queue_button_stops_active_job_without_resizing_and_keeps_rest_waiting(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    import clip_window
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    clips = settings.clip_window
    clips.open_file(str(sample))
    for _, val, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    clips.out.setValue(1.001)
    clips.enqueue()
    clips.in_spin().setValue(1.001)
    clips.out.setValue(1.702)
    clips.enqueue()
    entered = __import__('threading').Event()
    release = __import__('threading').Event()
    def prepare(source, start, end, cfg, target, cancellation=None, **kwargs):
        entered.set()
        while not release.wait(.01):
            cancellation.check()
        cancellation.check()
        target.write_bytes(b'candidate')
        return target
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    button_width = clips.render_queue.width()
    clips.start_queue()
    deadline = time.monotonic() + 5
    while not entered.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert entered.is_set()
    app.processEvents()
    assert clips.render_queue.text() == 'Стоп'
    assert clips.render_queue.width() == button_width
    assert clips.remove_job.width() == clips.render_queue.width()
    add_left = clips.queue.mapTo(clips, clips.queue.rect().topLeft()).x()
    add_right = clips.queue.mapTo(clips, clips.queue.rect().bottomRight()).x()
    remove_left = clips.remove_job.mapTo(clips, clips.remove_job.rect().topLeft()).x()
    render_right = clips.render_queue.mapTo(clips, clips.render_queue.rect().bottomRight()).x()
    assert remove_left == add_left
    assert render_right == add_right
    clips.render_queue.click()
    release.set()
    deadline = time.monotonic() + 5
    while clips.job_tokens and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    assert len(clips.waiting_jobs) == 1
    assert clips.render_queue.text() == 'Рендер очереди'
    assert clips.render_queue.width() == button_width
    assert clips.progress.isHidden()
    assert 'ошибка:' in clips.jobs.item(0).text().lower()
    clips.start_queue()
    deadline = time.monotonic() + 5
    while clips.job_tokens and time.monotonic() < deadline:
        release.set()
        app.processEvents()
        time.sleep(.01)
    assert not clips.job_tokens and not clips.waiting_jobs
    clips.close()
    settings.close()


def test_same_second_collisions_get_start_and_end_names(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    clips = settings.clip_window
    clips.open_file(str(sample))
    for _, val, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    clips.out.setValue(1.001)
    clips.enqueue()
    clips.in_spin().setValue(.5005)
    clips.out.setValue(1.5015)
    clips.enqueue()
    assert len(clips.waiting_jobs) == 2
    assert clips.waiting_jobs[0][0][1].endswith('_000_fade.mp4')
    assert clips.waiting_jobs[1][0][1].endswith('_000_001_fade.mp4')
    clips.enqueue()
    assert clips.waiting_jobs[2][0][1].endswith('_000_001_2_fade.mp4')
    for _ in range(3):
        clips.jobs.setCurrentRow(0)
        clips.remove_waiting_job()
    clips.close()
    settings.close()


def test_enabling_auto_render_starts_waiting_and_future_jobs(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    import clip_window
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    clips = settings.clip_window
    clips.open_file(str(sample))
    for _, val, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    calls = []
    def prepare(source, start, end, cfg, target, **kwargs):
        calls.append(start)
        target.write_bytes(b'candidate')
        return target
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    monkeypatch.setattr(clip_window, 'publish', lambda candidate, source, start, cfg: tmp_path / f'{len(calls)}.mp4')
    clips.out.setValue(1.001)
    clips.enqueue()
    assert not calls
    clips.auto_render.setChecked(True)
    clips.in_spin().setValue(1.001)
    clips.out.setValue(1.702)
    clips.enqueue()
    deadline = time.monotonic() + 5
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert len(calls) == 2 and not clips.waiting_jobs and not clips.active
    clips.close()
    settings.close()


def test_colliding_jobs_publish_separate_bytes(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    import clip_window
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    clips = settings.clip_window
    settings.folder.setText(str(tmp_path / 'exports'))
    clips.open_file(str(sample))
    for _, val, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    def prepare(source, start, end, cfg, target, **kwargs):
        target.write_bytes(str(start).encode())
        return target
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    clips.out.setValue(1.001)
    clips.enqueue()
    clips.in_spin().setValue(.5005)
    clips.out.setValue(1.5015)
    clips.enqueue()
    clips.start_queue()
    deadline = time.monotonic() + 5
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    files = sorted((tmp_path / 'exports').glob('*.mp4'))
    assert len(files) == 2
    assert len({f.read_bytes() for f in files}) == 2
    assert all('готово:' in clips.jobs.item(i).text() for i in range(2))
    # A later submission in the same session must not overwrite an earlier
    # completed clip merely because its start falls within the same second.
    clips.in_spin().setValue(.2)
    clips.out.setValue(1.702)
    clips.enqueue()
    clips.start_queue()
    deadline = time.monotonic() + 5
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert len(list((tmp_path / 'exports').glob('*.mp4'))) == 3
    assert all('готово:' in clips.jobs.item(i).text() for i in range(3))
    clips.close()
    settings.close()


def test_existing_unknown_result_is_not_overwritten(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    import clip_window
    from clip_plan import clip_name
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    clips = settings.clip_window
    settings.folder.setText(str(tmp_path))
    clips.open_file(str(sample))
    for _, val, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    clips.out.setValue(1.001)
    existing = tmp_path / clip_name(sample, settings.suffix.text(), 0)
    existing.write_bytes(b'previous result')
    def prepare(source, start, end, cfg, target, **kwargs):
        target.write_bytes(b'new result')
        return target
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    clips.enqueue()
    assert clips.waiting_jobs[0][0][1].endswith('_000_001_fade.mp4')
    clips.start_queue()
    deadline = time.monotonic() + 5
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert existing.read_bytes() == b'previous result'
    assert (tmp_path / clip_name(sample, settings.suffix.text(), 0, 1.001)).read_bytes() == b'new result'
    clips.close()
    settings.close()


def test_result_appearing_while_queued_is_not_overwritten(sample, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    import clip_window
    from clip_plan import clip_name
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    clips = settings.clip_window
    settings.folder.setText(str(tmp_path))
    clips.open_file(str(sample))
    for _, val, unit in settings.rows.values():
        unit.setCurrentIndex(unit.findData('seconds'))
        val.setValue(.1)
    clips.out.setValue(1.001)
    clips.enqueue()
    existing = tmp_path / clip_name(sample, settings.suffix.text(), 0)
    existing.write_bytes(b'created after enqueue')
    def prepare(source, start, end, cfg, target, **kwargs):
        target.write_bytes(b'candidate')
        return target
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    clips.start_queue()
    deadline = time.monotonic() + 5
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert existing.read_bytes() == b'created after enqueue'
    assert 'ошибка' in clips.jobs.item(0).text()
    assert clips.progress.value() < clips.progress.maximum()
    clips.close()
    settings.close()
