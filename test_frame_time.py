import pytest
from frame_time import format_frame_time, parse_frame_time


def test_elapsed_minutes_seconds_and_frames_round_trip():
    assert format_frame_time(145.8, 25) == '2:25:20'
    assert parse_frame_time('2:25:20', 25) == pytest.approx(145.8)
    assert format_frame_time(3601.4, 25) == '1:00:01:10'
    with pytest.raises(ValueError):
        parse_frame_time('2:25:25', 25)


def test_fractional_rate_labels_follow_elapsed_time_and_round_trip():
    fps = 30000 / 1001
    for n in (0, 1, 29, 30, 1798, 1799, 1800, 107892, 200000):
        label = format_frame_time(n / fps, fps)
        assert parse_frame_time(label, fps) == pytest.approx(n / fps, abs=1e-9)
    assert format_frame_time(3600.04, fps).startswith('1:00:00:')


def test_time_fields_accept_frame_labels_and_step_one_frame(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    field = window.clips.in_spin()
    field.set_fps(25)
    field.setValue(145.8)
    assert field.text() == '2:25:20'
    field.lineEdit().setText('2:25:21')
    field.interpretText()
    assert field.value() == pytest.approx(145.84)
    field.stepUp()
    assert field.text() == '2:25:22'
    assert field.value() == pytest.approx(145.88)
    assert window.close()
