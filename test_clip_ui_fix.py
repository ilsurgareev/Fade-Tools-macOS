import tempfile
import time
import threading
import sys

from test_fade import sample

app_ref = None


def test_close_retries_when_temporary_directory_is_locked(tmp_path, monkeypatch):
    global app_ref
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from settings_app import Settings
    app_ref = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.open_clips()
    window = settings.clip_window
    cleanup = window.temp.cleanup
    attempts = []
    callbacks = []
    def temporarily_locked():
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError('temporary directory locked')
        cleanup()
    monkeypatch.setattr(window.temp, 'cleanup', temporarily_locked)
    monkeypatch.setattr(QTimer, 'singleShot', lambda delay, callback: callbacks.append(callback))
    assert window.close()
    assert len(attempts) == 1 and callbacks
    callbacks.pop()()
    assert len(attempts) == 2
    assert not window.temp.name or not __import__('pathlib').Path(window.temp.name).exists()
    settings.close()


def test_cancellation_stops_owned_process(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    from clip_engine import Cancellation, PreviewCancelled
    token = Cancellation()
    outcome = []
    def work():
        try:
            token.run([sys.executable, '-c', 'import time; time.sleep(30)'])
        except PreviewCancelled:
            outcome.append('cancelled')
    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 3
    while token.process is None and time.monotonic() < deadline:
        time.sleep(.01)
    assert token.process is not None
    token.cancel()
    thread.join(3)
    assert not thread.is_alive()
    assert outcome == ['cancelled']


def test_changing_marks_cancels_old_preview_and_prepares_latest(sample, tmp_path, monkeypatch):
    global app_ref
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import Settings
    from clip_engine import PreviewCancelled
    import clip_window
    app = QApplication.instance() or QApplication([])
    app_ref = app
    started = threading.Event()
    outcomes = []
    def controlled_prepare(source, start, end, cfg, target, cancellation=None, analysis_cache=None):
        if not started.is_set():
            started.set()
            try:
                cancellation.run([sys.executable, '-c', 'import time; time.sleep(30)'])
            except PreviewCancelled:
                outcomes.append('cancelled')
                raise
        target.write_bytes(b'placeholder preview')
        outcomes.append((start, end))
        return target
    monkeypatch.setattr(clip_window, 'prepare', controlled_prepare)
    settings = Settings()
    settings.show()
    settings.open_clips()
    window = settings.clip_window
    window.open_file(str(sample))
    window.render_preview()
    assert started.wait(3)
    window.in_spin().setValue(.501)
    window.out.setValue(1.502)
    deadline = time.monotonic() + 8
    window.play_exact()
    while window.candidate is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert outcomes[0] == 'cancelled'
    assert window.candidate is not None
    assert abs(outcomes[1][0] - .5005) < .001
    assert abs(outcomes[1][1] - 1.5015) < .001
    window.close()
    settings.close()


def test_close_while_preview_is_preparing(sample, tmp_path, monkeypatch):
    global app_ref
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication, QMessageBox
    from settings_app import Settings
    import clip_window
    app = QApplication.instance() or QApplication([])
    app_ref = app
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: None)
    def slow_prepare(*args, **kwargs):
        time.sleep(1)
        raise RuntimeError('test stopped')
    monkeypatch.setattr(clip_window, 'prepare', slow_prepare)
    settings = Settings()
    settings.show()
    settings.open_clips()
    window = settings.clip_window
    window.open_file(str(sample))
    window.render_preview()
    assert window.active
    assert window.close(), 'The clip window must close even while a preview is being prepared'
    assert not window.isVisible()
    deadline = time.monotonic() + 5
    while window.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    # Reopening the workspace after cancelling must not break playback.
    from clip_engine import prepare
    monkeypatch.setattr(clip_window, 'prepare', prepare)
    settings.engine.setCurrentText('CPU')
    for _, value, _ in settings.rows.values():
        value.setValue(.2)
    settings.open_clips()
    reopened = settings.clip_window
    reopened.open_file(str(sample))
    reopened.in_spin().setValue(.501)
    reopened.out.setValue(1.502)
    reopened.play_exact()
    deadline = time.monotonic() + 30
    while not reopened.candidate and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert reopened.candidate, reopened.notice.text()
    assert reopened.preview_mode
    reopened.close()
    deadline = time.monotonic() + 6
    while settings.detached_clips and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert not settings.detached_clips
    settings.close()
