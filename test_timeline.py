import tempfile

from test_fade import sample

app_ref = None


def test_initial_range_spans_the_track_with_unclipped_handles(tmp_path, monkeypatch):
    app = app_for_test(tmp_path, monkeypatch)
    from clip_window import RangeSlider
    slider = RangeSlider()
    slider.resize(700, 68)
    slider.show()
    app.processEvents()
    assert slider.selection == (slider.minimum(), slider.maximum())
    assert slider.marker_rect('in').top() >= 3
    assert slider.marker_rect('out').bottom() <= slider.height() - 4
    assert slider.marker_rect('in').center().x() < slider.width() / 10
    assert slider.marker_rect('out').center().x() > slider.width() * .9
    slider.close()


def app_for_test(tmp_path, monkeypatch):
    global app_ref
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    app_ref = QApplication.instance() or QApplication([])
    return app_ref


def test_drag_in_out_and_click_to_seek(tmp_path, monkeypatch):
    app = app_for_test(tmp_path, monkeypatch)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from clip_window import RangeSlider
    slider = RangeSlider()
    slider.resize(700, 60)
    slider.setRange(0, 100000)
    slider.set_selection(20000, 70000)
    slider.show()
    app.processEvents()
    moved = []
    slider.sliderMoved.connect(moved.append)
    start = slider.marker_rect('in').center()
    end = QPoint(slider.value_to_x(40000), start.y())
    QTest.mousePress(slider, Qt.LeftButton, pos=start)
    QTest.mouseMove(slider, end)
    QTest.mouseRelease(slider, Qt.LeftButton, pos=end)
    assert abs(slider.selection[0] - 40000) < 300
    assert slider.selection[1] == 70000
    start = slider.marker_rect('out').center()
    end = QPoint(slider.value_to_x(80000), start.y())
    QTest.mousePress(slider, Qt.LeftButton, pos=start)
    QTest.mouseMove(slider, end)
    QTest.mouseRelease(slider, Qt.LeftButton, pos=end)
    assert abs(slider.selection[1] - 80000) < 300
    start = slider.marker_rect('in').center()
    end = QPoint(slider.value_to_x(95000), start.y())
    QTest.mousePress(slider, Qt.LeftButton, pos=start)
    QTest.mouseMove(slider, end)
    QTest.mouseRelease(slider, Qt.LeftButton, pos=end)
    assert slider.selection[0] == slider.selection[1]
    assert not slider.marker_rect('in').intersects(slider.marker_rect('out'))
    point = QPoint(slider.value_to_x(55000), slider.groove_y())
    QTest.mouseClick(slider, Qt.LeftButton, pos=point)
    assert moved and abs(moved[-1] - 55000) < 300
    slider.close()


def test_seeking_outside_preview_returns_to_source(sample, tmp_path, monkeypatch):
    app = app_for_test(tmp_path, monkeypatch)
    from settings_app import Settings
    settings = Settings()
    settings.open_clips()
    window = settings.clip_window
    window.open_file(str(sample))
    assert window.slider.selection == (0, window.slider.maximum())
    assert window.slider.isEnabled()
    window.preview_mode = True
    window.preview_start = .5
    window.preview_end = 1.5
    switches = []
    monkeypatch.setattr(window, 'show_original', lambda position=None, resume=False: switches.append((position, resume)))
    window.seek(2000)
    assert switches and switches[0][0] == window._frame_milliseconds(2)
    window.close()
    settings.close()


def test_source_and_preview_seek_with_real_player(sample, tmp_path, monkeypatch):
    import time
    app = app_for_test(tmp_path, monkeypatch)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from settings_app import Settings
    settings = Settings()
    settings.engine.setCurrentText('CPU')
    for _, value, _ in settings.rows.values():
        value.setValue(.2)
    settings.show()
    settings.open_clips()
    window = settings.clip_window
    window.open_file(str(sample))
    deadline = time.monotonic() + 15
    while not window.player.isSeekable() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert window.player.isSeekable()
    point = QPoint(window.slider.value_to_x(2200), window.slider.groove_y())
    QTest.mouseClick(window.slider, Qt.LeftButton, pos=point)
    while abs(window.player.position() - 2200) > 150 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert abs(window.player.position() - 2200) <= 150
    window.in_spin().setValue(.501)
    window.out.setValue(1.502)
    window.play_exact()
    while not window.candidate and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert window.candidate, window.notice.text()
    assert window.preview_mode
    while (not window.player.isSeekable() or window.player.duration() == 0) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    window.player.pause()
    point = QPoint(window.slider.value_to_x(1000), window.slider.groove_y())
    QTest.mouseClick(window.slider, Qt.LeftButton, pos=point)
    while abs(window.player.position() - 500) > 150 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert window.preview_mode
    assert abs(window.player.position() - 500) <= 150
    point = QPoint(window.slider.value_to_x(2200), window.slider.groove_y())
    QTest.mouseClick(window.slider, Qt.LeftButton, pos=point)
    while (window.preview_mode or abs(window.player.position() - 2200) > 150) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert not window.preview_mode
    assert abs(window.player.position() - 2200) <= 150
    window.close()
    settings.close()


def test_drag_marker_updates_numeric_field_and_defers_preview(sample, tmp_path, monkeypatch):
    app = app_for_test(tmp_path, monkeypatch)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from settings_app import Settings
    settings = Settings()
    settings.open_clips()
    window = settings.clip_window
    window.open_file(str(sample))
    app.processEvents()
    slider = window.slider
    start = slider.marker_rect('in').center()
    target = QPoint(slider.value_to_x(500), start.y())
    QTest.mousePress(slider, Qt.LeftButton, pos=start)
    QTest.mouseMove(slider, target)
    assert abs(window.in_spin().value() - .5) < .02
    assert not window.timer.isActive()
    QTest.mouseRelease(slider, Qt.LeftButton, pos=target)
    assert not window.timer.isActive()
    assert window.live_snapshot is not None
    window.close()
    settings.close()
