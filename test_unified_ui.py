import tempfile
from pathlib import Path


def test_space_controls_playback_with_focus_in_either_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.show()
    main.activateWindow()
    app.processEvents()
    calls = []
    monkeypatch.setattr(main.clips, 'play', lambda: calls.append('toggle'))
    for control in (main.clips.in_spin(), main.settings.suffix,
                    main.settings.rows['video_in'][0]):
        control.setFocus()
        app.processEvents()
        QTest.keyClick(control, Qt.Key_Space)
        app.processEvents()
    assert calls == ['toggle', 'toggle', 'toggle']
    assert main.close()


def test_mark_controls_share_playback_row(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.resize(1600, 900)
    main.show()
    app.processEvents()
    clips = main.clips
    assert clips.in_spin().geometry().center().y() == clips.position_label.geometry().center().y()
    assert clips.out.geometry().center().y() == clips.in_spin().geometry().center().y()
    assert main.close()


def test_video_surface_accepts_single_local_file_drop(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.show()
    app.processEvents()
    opened = []
    monkeypatch.setattr(main.clips, 'open_file', opened.append)
    file = tmp_path / 'clip with spaces.mp4'
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    surface = main.clips.video.viewport()
    enter = QDragEnterEvent(QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(surface, enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(surface, drop)
    assert drop.isAccepted()
    assert [Path(p) for p in opened] == [file]
    multiple = QMimeData()
    multiple.setUrls([QUrl.fromLocalFile(str(file)), QUrl.fromLocalFile(str(tmp_path / 'other.mp4'))])
    rejected = QDragEnterEvent(QPoint(20, 20), Qt.CopyAction, multiple, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(surface, rejected)
    assert not rejected.isAccepted()
    main.close()


def test_single_window_with_video_left_and_settings_right(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication, QPushButton
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.resize(1600, 900)
    main.show()
    app.processEvents()
    assert main.clips.window() is main
    assert main.settings.window() is main
    assert main.splitter.indexOf(main.clips) == 0
    assert main.splitter.indexOf(main.settings_scroll) == 1
    assert main.clips.isVisible() and main.settings.isVisible()
    assert not main.clips.preview_button.isEnabled()
    assert main.clips.in_spin().accessibleName() == 'In: минуты, секунды, кадры'
    assert main.clips.out.accessibleName() == 'Out: минуты, секунды, кадры'
    main.splitter.setSizes([1000, 600])
    app.processEvents()
    before = main.splitter.sizes()[0]
    main.splitter.setSizes([700, 900])
    app.processEvents()
    assert main.splitter.sizes()[0] < before
    assert not any('Отрывки' in button.text() for button in main.settings.findChildren(QPushButton))
    main.settings.rows['video_in'][2].setCurrentIndex(main.settings.rows['video_in'][2].findData('seconds'))
    main.settings.rows['video_in'][1].setValue(.7)
    assert main.settings.data()['fades']['video_in']['value'] == .7
    assert main.close()


def test_update_control_reports_errors_and_offers_new_release(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    settings = main.settings
    assert settings.update_button.text() == 'Проверить обновления'
    settings._update_checked(None, 'offline')
    assert 'offline' in settings.update_status.text()
    offered = []
    settings._offer_update = lambda: offered.append(True)
    settings._update_checked({
        'version': '0.3.0', 'version_tuple': (0, 3, 0), 'tag': 'macos-v0.3.0',
    }, '')
    assert settings.update_button.text() == 'Обновить до 0.3.0'
    assert offered == [True]
    assert main.close()


def test_unsaved_settings_keep_both_panels_open(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication, QMessageBox
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.show()
    app.processEvents()
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    actual_changed = main.settings.changed
    main.settings.status.setText('Не сохранено: тест')
    monkeypatch.setattr(main.settings, 'changed', lambda *args: False)
    assert not main.close()
    assert main.isVisible() and main.clips.isVisible() and main.settings.isVisible()
    monkeypatch.setattr(main.settings, 'changed', actual_changed)
    assert main.close()


def test_main_window_closes_while_preview_is_running(tmp_path, monkeypatch):
    import time
    # A file-independent slow worker exercises the real close path without media writes.
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.show()
    app.processEvents()
    clips = main.clips
    def slow():
        time.sleep(.4)
        return None
    clips.launch(slow, lambda result, error: None)
    assert clips.active == 1
    assert main.close()
    assert not main.isVisible()
    deadline = time.monotonic() + 3
    while clips.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert clips.active == 0


def test_settings_change_updates_the_embedded_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME', str(tmp_path / 'settings'))
    from PySide6.QtWidgets import QApplication
    from settings_app import MainWindow
    app = QApplication.instance() or QApplication([])
    main = MainWindow()
    main.show()
    app.processEvents()
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'test')
    main.clips.source = source
    main.clips.fps = 25
    before = main.clips.generation
    main.settings.rows['video_out'][1].setValue(.7)
    assert main.clips.generation > before
    assert not main.clips.timer.isActive()
    assert main.close()
