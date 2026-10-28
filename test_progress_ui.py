def test_progress_bar_and_result_folder(tmp_path,monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME',str(tmp_path/'settings'))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from clip_window import Cancellation,QDesktopServices
    from settings_app import Settings
    app=QApplication.instance() or QApplication([])
    settings=Settings();settings.open_clips();clips=settings.clip_window
    clips.show();app.processEvents()
    token=Cancellation();token.phase('Кодирование NVIDIA NVENC',0,1);token.fraction=.42
    clips.preview_cancel=token;clips.update_jobs()
    assert clips.progress.isVisible() and clips.progress.value()==420
    assert '42%' in clips.processing.text()
    token.stage='Чтение метаданных';clips.update_jobs()
    assert clips.progress.maximum()==1000 and clips.progress.value()==420
    clips.preview_cancel=None;clips.update_jobs()
    assert clips.progress.isHidden()
    calls=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:calls.append(url.toLocalFile()))
    clips.last_output=tmp_path/'clip.mp4';clips.open_output_folder()
    assert calls==[str(tmp_path).replace('\\','/')]
    clips.close();settings.close()

def test_pending_render_without_preview_is_protected_on_close(tmp_path,monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    monkeypatch.setenv('FADE_TOOLS_HOME',str(tmp_path/'settings'))
    from PySide6.QtWidgets import QApplication,QMessageBox
    from settings_app import MainWindow
    app=QApplication.instance() or QApplication([])
    main=MainWindow();main.show();app.processEvents()
    messages=[]
    monkeypatch.setattr(QMessageBox,'information',lambda *args:messages.append(args[2]))
    main.clips.job_tokens.append((None,'queued',None))
    assert not main.clips.queued_previews
    assert not main.close()
    assert messages and main.isVisible()
    main.clips.job_tokens.clear()
    assert main.close()
