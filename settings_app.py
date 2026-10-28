"""Small native settings editor. No processing service is kept running."""
import copy
import os
import sys

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPolygonF
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget, QSpinBox, QSplitter, QScrollArea, QFrame,
                               QStyle, QStyleOptionButton, QStyleOptionComboBox, QStylePainter)

from config import DEFAULT, KEYS, app_dir, load, save, seconds

LABELS = ('Video Fade In', 'Video Fade Out', 'Audio Fade In', 'Audio Fade Out')


class VisibleCheckBox(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked():
            option = QStyleOptionButton()
            self.initStyleOption(option)
            rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor('#edf1f7'), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(QPolygonF([
                QPointF(rect.x() + rect.width() * x, rect.y() + rect.height() * y)
                for x, y in ((.23, .51), (.43, .72), (.78, .28))
            ]))


class CenteredComboBox(QComboBox):
    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text = option.currentText
        option.currentText = ''
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)
        rect = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self)
        painter.drawText(rect, Qt.AlignCenter, text)


class Envelope(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setMinimumHeight(112)
        self.setAccessibleName('Кривые видео и звука, пример 10 секунд при 25 FPS')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width() - 95
        cfg = self.window.data()
        for row, prefix, color, label in ((0, 'video', '#0a84ff', 'Видео'), (1, 'audio', '#8e8e94', 'Звук')):
            y = 8 + row * 46
            p.setPen(QColor('#8e8e94'))
            p.drawText(0, y + 20, label)
            p.setPen(QPen(QColor('#3e3e45'), 1))
            p.drawLine(70, y + 34, 70 + w, y + 34)
            a = seconds(cfg['fades'][prefix + '_in'], 25)
            b = seconds(cfg['fades'][prefix + '_out'], 25)
            points = []
            for i in range(201):
                t = i / 20
                gain = min(1, t / a) if a else 1
                if b:
                    gain *= max(0, min(1, (10 - t) / b))
                points.append(QPointF(70 + w * i / 200, y + 34 - gain * 28))
            p.setPen(QPen(QColor(color), 2))
            p.drawPolyline(QPolygonF(points))
        p.setPen(QColor('#8e8e94'))
        p.drawText(70, 108, 'Пример: 10 с · 25 FPS. Реальный FPS берётся из файла / таймлайна.')


class Settings(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Fade Settings')
        self.setMinimumWidth(620)
        self.loading = True
        self.load_error = ''
        try:
            self.cfg = load()
        except Exception as exc:
            self.cfg = copy.deepcopy(DEFAULT)
            self.load_error = str(exc)
        self.setStyleSheet('''
            QWidget {background:#1c1c20; color:#f2f2f7; font-size:9pt;}
            QLabel#title {font-size:18pt; font-weight:600;}
            QLabel#muted, QLabel#section {color:#8e8e94;}
            QLabel#section {font-size:8pt;}
            QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox {background:#16161a; border:1px solid #3e3e45;
                border-radius:0; padding:4px; min-height:20px; selection-background-color:#0058b2;}
            QLineEdit:focus,QDoubleSpinBox:focus,QSpinBox:focus,QComboBox:focus,QPushButton:focus {border:1px solid #0a84ff;}
            QPushButton {background:#36363c; border:1px solid #3e3e45; border-radius:0; padding:5px 10px; font-weight:600;}
            QPushButton:hover {background:#44444b;}
            QPushButton:pressed {background:#28282e;}
            QPushButton#primary {background:#0a84ff; border-color:#0a84ff; color:white;}
            QPushButton#primary:hover {background:#2996ff;}
            QPushButton#primary:pressed {background:#0058b2;}
            QPushButton#primary:disabled,QPushButton:disabled {background:#29292f; border-color:#383840; color:#85858c;}
            QCheckBox {spacing:8px;}
            QCheckBox::indicator {width:14px; height:14px;}
            QCheckBox::indicator:unchecked {border:1px solid #62626a; background:#16161a;}
            QCheckBox::indicator:checked {border:1px solid #0a84ff; background:#0a84ff;}
            QDoubleSpinBox:disabled,QComboBox:disabled {color:#75757b; background:#202024;}
        ''')
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(8)
        title = QLabel('Fade Settings')
        title.setObjectName('title')
        root.addWidget(title)
        description = QLabel('Настройки для отрывков и Fade Droplet.')
        description.setObjectName('muted')
        root.addWidget(description)
        update_row = QHBoxLayout()
        self.update_status = QLabel('Проверка обновлений при запуске')
        self.update_status.setObjectName('muted')
        self.update_status.setWordWrap(True)
        update_row.addWidget(self.update_status, 1)
        self.update_button = QPushButton('Проверить обновления')
        self.update_button.setToolTip('Проверить публичный канал обновлений Fade Tools для macOS')
        self.update_button.clicked.connect(self.check_updates)
        update_row.addWidget(self.update_button)
        root.addLayout(update_row)
        presets = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItem('Пресеты…')
        self.preset.addItem('По умолчанию')
        self.preset.addItems(list(self.cfg['presets']))
        if 'Стандартный' in self.cfg['presets']:
            self.preset.setCurrentText('Стандартный')
        self.preset.activated.connect(self.use_preset)
        presets.addWidget(self.preset, 1)
        btn = QPushButton('Сохранить пресет')
        btn.clicked.connect(self.add_preset)
        presets.addWidget(btn)
        remove = QPushButton('Удалить')
        remove.clicked.connect(self.delete_preset)
        presets.addWidget(remove)
        root.addLayout(presets)
        fade_heading = QLabel('FADE')
        fade_heading.setObjectName('section')
        root.addWidget(fade_heading)
        grid = QGridLayout()
        grid.setVerticalSpacing(5)
        self.rows = {}
        for n, (key, label) in enumerate(zip(KEYS, LABELS)):
            on = VisibleCheckBox(label)
            val = QDoubleSpinBox()
            val.setAlignment(Qt.AlignCenter)
            val.setRange(0, 100000)
            val.setKeyboardTracking(False)
            unit = CenteredComboBox()
            unit.addItem('Секунды', 'seconds')
            unit.addItem('Кадры', 'frames')
            val.setAccessibleName(label + ': длительность')
            unit.setAccessibleName(label + ': единицы')
            self.rows[key] = on, val, unit
            grid.addWidget(on, n, 0)
            grid.addWidget(val, n, 1)
            grid.addWidget(unit, n, 2)
            on.toggled.connect(lambda checked, k=key: self.row_change(k))
            unit.currentIndexChanged.connect(lambda index, k=key: self.row_change(k))
            val.valueChanged.connect(self.changed)
        grid.setColumnStretch(0, 1)
        root.addLayout(grid)
        self.envelope = Envelope(self)
        root.addWidget(self.envelope)
        export_heading = QLabel('ЭКСПОРТ')
        export_heading.setObjectName('section')
        root.addWidget(export_heading)
        form = QFormLayout()
        form.setSpacing(6)
        self.codec = QComboBox()
        self.codec.addItem('H.265')
        self.codec.setEnabled(False)
        self.engine = QComboBox()
        self.engine.addItems(['Apple VideoToolbox', 'CPU'] if os.name != 'nt' else ['CPU', 'NVENC'])
        self.engine.setToolTip('Apple VideoToolbox — аппаратное кодирование; при ошибке автоматически используется CPU.')
        self.quality = QComboBox()
        for text, value in [('UHQ · Very Slow · Full multipass', 'master')]:
            self.quality.addItem(text, value)
        self.quality.setEnabled(False)
        self.bitrate = QSpinBox()
        self.bitrate.setRange(30, 1000)
        self.bitrate.setSuffix(' Мбит/с')
        self.bitrate.lineEdit().setTextMargins(6, 0, 0, 0)
        self.bitrate.setKeyboardTracking(False)
        self.bitrate.setToolTip('Цель VBR для видеопотока. Фактическое среднее зависит от содержимого; это не гарантированный нижний предел.')
        self.bitrate.setAccessibleName('Целевой битрейт видео, Мбит/с')
        codec_row = QHBoxLayout()
        codec_row.addWidget(self.codec, 1)
        codec_row.addWidget(self.engine)
        form.addRow('Кодек', codec_row)
        form.addRow('Качество', self.quality)
        form.addRow('Битрейт VBR', self.bitrate)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText('Рядом с исходным файлом')
        folder_row.addWidget(self.folder)
        browse = QPushButton('Выбрать…')
        browse.clicked.connect(self.browse)
        folder_row.addWidget(browse)
        form.addRow('Папка', folder_row)
        self.suffix = QLineEdit()
        self.suffix.setPlaceholderText('Необязательно')
        self.suffix.setToolTip('Можно оставить пустым: в имени отрывка уже есть время начала.')
        form.addRow('Суффикс', self.suffix)
        root.addLayout(form)
        hint = QLabel('MP4 · H.265 · AAC · Размер, FPS и 8/10 бит из исходника\n'
                      'HDR, альфа-канал, поворот и субтитры пока не поддерживаются.')
        hint.setObjectName('muted')
        root.addWidget(hint)
        self.notifications = VisibleCheckBox('Уведомлять об успешном завершении')
        root.addWidget(self.notifications)
        root.addSpacing(8)
        actions = QHBoxLayout()
        actions.setSpacing(14)
        reset = QPushButton('Сбросить настройки')
        reset.clicked.connect(self.reset)
        actions.addWidget(reset)
        logs = QPushButton('Открыть логи')
        logs.clicked.connect(self.open_logs)
        actions.addWidget(logs)
        root.addLayout(actions)
        root.addSpacing(4)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)
        for widget in (self.codec, self.engine, self.quality):
            widget.currentIndexChanged.connect(self.changed)
        self.folder.textChanged.connect(self.changed)
        self.suffix.textChanged.connect(self.changed)
        self.notifications.toggled.connect(self.changed)
        self.bitrate.valueChanged.connect(self.changed)
        self.populate(self.cfg)
        self.loading = False
        if self.load_error:
            self.status.setText('Ошибка чтения: ' + self.load_error + '\nИсправьте настройки или нажмите «Сбросить».')
            self.status.setStyleSheet('color:#ffb5ae')
        else:
            self.changed()  # first run creates the shared config
        self._release = None
        from updater import UpdateService
        self._update_service = UpdateService(self)
        self._update_service.check_finished.connect(self._update_checked)
        self._update_service.download_progress.connect(self._download_progress)
        self._update_service.download_finished.connect(self._update_downloaded_from_service)
        self._install_app_path = None
        self._install_version = None
        QTimer.singleShot(1600, self.check_updates)

    def check_updates(self):
        if self._install_app_path is not None:
            return
        if self._release is not None:
            self._offer_update()
            return
        from updater import APP_VERSION
        self.update_button.setEnabled(False)
        self.update_button.setText('Проверяем…')
        self.update_status.setText(f'Проверяем обновления · версия {APP_VERSION}')
        self.update_status.setStyleSheet('color:#8e8e94')
        self._update_service.check()

    def _update_checked(self, release, error):
        self.update_button.setEnabled(True)
        if error:
            self.update_button.setText('Проверить ещё раз')
            self.update_status.setText('Не удалось проверить обновления: ' + error)
            self.update_status.setStyleSheet('color:#ffb5ae')
            self.update_button.setToolTip(error)
            return
        from updater import APP_VERSION, version_tuple
        if release['version_tuple'] <= version_tuple(APP_VERSION):
            self._release = None
            self.update_button.setText('Проверить обновления')
            self.update_status.setText(f'Установлена последняя версия · {APP_VERSION}')
            self.update_status.setStyleSheet('color:#30d158')
            self.update_button.setToolTip('Публичный канал проверен: новых версий нет')
            return
        self._release = release
        self.update_button.setText(f"Обновить до {release['version']}")
        self.update_button.setObjectName('primary')
        self.update_button.setToolTip(f"Доступна Fade Tools {release['version']}")
        self.update_status.setText(f"Доступно обновление · {release['version']}")
        self.update_status.setStyleSheet('color:#ffd60a')
        self._offer_update()

    def _offer_update(self):
        from PySide6.QtWidgets import QMessageBox
        from updater import APP_VERSION
        release = self._release
        if release is None:
            return
        answer = QMessageBox.question(
            self,
            'Доступно обновление Fade Tools',
            f"Установлена версия {APP_VERSION}. Доступна версия {release['version']}.\n\n"
            'Скачать и установить сейчас? Приложение перезапустится после установки.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._download_update(release)

    def _download_update(self, release):
        import os
        import tempfile
        from pathlib import Path
        from updater import installed_app_path
        app_path = installed_app_path()
        if app_path is None:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(release['page']))
            self.update_status.setText('Открыл страницу релиза для установки вручную')
            return
        self.update_button.setEnabled(False)
        self.update_button.setText('Загрузка… 0%')
        self.update_status.setText('Загрузка и проверка архива обновления…')
        descriptor, path = tempfile.mkstemp(prefix=f"fade-tools-{release['tag']}-", suffix='.zip')
        os.close(descriptor)
        destination = Path(path)
        self._install_app_path = app_path
        self._install_version = release['version']
        self._update_service.download(release, destination)

    def _download_progress(self, percent):
        self.update_button.setText(f'Загрузка… {percent}%')

    def _update_downloaded(self, archive, error, app_path, version):
        from PySide6.QtWidgets import QMessageBox, QApplication
        from updater import start_installer
        self.update_button.setEnabled(True)
        if error:
            self.update_button.setText('Повторить обновление')
            self.update_status.setText('Не удалось загрузить обновление: ' + error)
            self.update_status.setStyleSheet('color:#ffb5ae')
            return
        try:
            start_installer(archive, app_path, version)
        except Exception as exc:
            archive.unlink(missing_ok=True)
            self.update_button.setText(f'Обновить до {version}')
            self.update_status.setText('Не удалось подготовить установку: ' + str(exc))
            self.update_status.setStyleSheet('color:#ffb5ae')
            QMessageBox.warning(self, 'Не удалось установить обновление', str(exc))
            return
        self.update_button.setText('Перезапуск…')
        self.update_status.setText('Архив проверен. Fade Tools перезапустится с новой версией.')
        QApplication.quit()

    def _update_downloaded_from_service(self, archive, error):
        app_path, version = self._install_app_path, self._install_version
        self._install_app_path = None
        self._install_version = None
        if app_path is None or version is None:
            return
        self._update_downloaded(archive, error, app_path, version)

    def populate(self, cfg):
        self.loading = True
        for key, (on, val, unit) in self.rows.items():
            f = cfg['fades'][key]
            on.setChecked(f['enabled'])
            unit.setCurrentIndex(unit.findData(f['unit']))
            val.setDecimals(0 if f['unit'] == 'frames' else 3)
            val.setSingleStep(1 if f['unit'] == 'frames' else 0.1)
            val.setValue(f['value'])
            val.setEnabled(f['enabled'])
            unit.setEnabled(f['enabled'])
        self.codec.setCurrentText(cfg['codec'])
        self.engine.setCurrentText(cfg['engine'])
        self.quality.setCurrentIndex(0)
        self.bitrate.setValue(round(cfg['video_bitrate_mbps']))
        self.folder.setText(cfg['output_dir'])
        self.suffix.setText(cfg['suffix'])
        self.notifications.setChecked(cfg['notifications'])
        self.loading = False

    def data(self):
        d = copy.deepcopy(self.cfg)
        d['fades'] = {key: {'enabled': on.isChecked(), 'value': val.value(),
                            'unit': unit.currentData()} for key, (on, val, unit) in self.rows.items()}
        d.update(codec=self.codec.currentText(), engine=self.engine.currentText(),
                 quality=self.quality.currentData(), output_dir=self.folder.text().strip(),
                 video_bitrate_mbps=self.bitrate.value(),
                 suffix=self.suffix.text(), notifications=self.notifications.isChecked())
        return d

    def row_change(self, key):
        if self.loading:
            return
        on, val, unit = self.rows[key]
        was = self.loading
        self.loading = True
        val.setEnabled(on.isChecked())
        unit.setEnabled(on.isChecked())
        val.setDecimals(0 if unit.currentData() == 'frames' else 3)
        val.setSingleStep(1 if unit.currentData() == 'frames' else 0.1)
        self.loading = was
        self.changed()

    def changed(self, *args):
        if self.loading:
            return
        try:
            cfg = self.data()
            self.engine.setEnabled(cfg['codec'] in ('H.264', 'H.265'))
            self.quality.setItemText(0, 'UHQ · p7 Very Slow · Full multipass' if cfg['engine'] == 'NVENC'
                                     else ('Apple · аппаратный VBR · резерв CPU' if cfg['engine'] == 'Apple VideoToolbox' else 'CPU · Very Slow · целевой VBR'))
            save(cfg)
            self.cfg = cfg
            self.status.setText('Сохранено автоматически · Изменения применятся к следующему запуску')
            self.status.setStyleSheet('color:#30d158')
            success = True
        except Exception as exc:
            self.status.setText('Не сохранено: ' + str(exc))
            self.status.setStyleSheet('color:#ffb5ae')
            success = False
        self.envelope.update()
        return success

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Папка результатов', self.folder.text())
        if folder:
            self.folder.setText(folder)

    def add_preset(self):
        name, ok = QInputDialog.getText(self, 'Новый пресет', 'Название')
        if ok and name.strip() and name.strip() not in ('Пресеты…', 'По умолчанию'):
            snapshot = self.data()
            snapshot.pop('presets')
            self.cfg['presets'][name.strip()] = snapshot
            if self.preset.findText(name.strip()) < 0:
                self.preset.addItem(name.strip())
            self.preset.setCurrentText(name.strip())
            self.changed()

    def delete_preset(self):
        name = self.preset.currentText()
        if name in self.cfg['presets']:
            del self.cfg['presets'][name]
            self.preset.removeItem(self.preset.currentIndex())
            self.changed()

    def use_preset(self, index):
        if index == 1:
            self.reset()
        elif index > 1:
            cfg = copy.deepcopy(self.cfg['presets'][self.preset.currentText()])
            cfg['presets'] = self.cfg['presets']
            self.populate(cfg)
            self.changed()

    def reset(self):
        presets = self.cfg['presets']
        self.cfg = copy.deepcopy(DEFAULT)
        self.cfg['presets'] = presets
        self.populate(self.cfg)
        self.changed()

    def open_logs(self):
        folder = app_dir() / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_clips(self):
        from clip_window import ClipWindow
        if not getattr(self, 'clip_window', None):
            self.clip_window = ClipWindow(self)
            self.clip_window.setStyleSheet(self.styleSheet())
        self.clip_window.show()
        self.clip_window.raise_()
        self.clip_window.activateWindow()

    def closeEvent(self, event):
        current = getattr(self, 'clip_window', None)
        windows = list(getattr(self, 'detached_clips', [])) + ([current] if current else [])
        if any(getattr(w, 'job_tokens', []) for w in windows):
            QMessageBox.information(self, 'Очередь', 'Сначала выполните задания или удалите ожидающие из очереди.')
            event.ignore()
            return
        # Commit typed values even if the user closes while a field has focus.
        for on, val, unit in self.rows.values():
            val.interpretText()
        self.bitrate.interpretText()
        self.changed()
        if self.status.text().startswith('Не сохранено:'):
            QMessageBox.warning(self, 'Настройки не сохранены', self.status.text())
            event.ignore()
        else:
            window = getattr(self, 'clip_window', None)
            if window and not window.close():
                event.ignore()
            else:
                event.accept()


class MainWindow(QWidget):
    """One resizable workspace: clip editor on the left, shared settings on the right."""
    def __init__(self):
        super().__init__()
        from clip_window import ClipWindow
        self.setWindowTitle('Fade Tools')
        self.settings = Settings()
        self.settings.setMinimumWidth(380)
        self.setStyleSheet(self.settings.styleSheet())
        self.clips = ClipWindow(self.settings)
        self.settings.clip_window = self.clips
        QApplication.instance().installEventFilter(self)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setFrameShape(QFrame.NoFrame)
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setMinimumWidth(380)
        self.settings_scroll.setWidget(self.settings)
        self.splitter.addWidget(self.clips)
        self.splitter.addWidget(self.settings_scroll)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.splitter)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        if available:
            self.resize(min(1680, available.width()), min(960, available.height()))
        else:
            self.resize(1440, 900)
        self.splitter.setSizes([1000, 680])

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space
                and event.modifiers() == Qt.NoModifier and self.isActiveWindow()
                and isinstance(watched, QWidget) and watched.window() is self):
            if not event.isAutoRepeat():
                self.clips.play()
            return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if self.settings.close():
            QApplication.instance().removeEventFilter(self)
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

