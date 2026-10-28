"""Finder file-open and drag-and-drop entry point for the portable macOS droplet."""
import sys
from pathlib import Path
from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, Signal, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog
from droplet import main as process_batch


class Application(QApplication):
    opened = Signal(str)

    def event(self, event):
        if event.type() == QEvent.FileOpen:
            self.opened.emit(event.file())
            return True
        return super().event(event)


class Signals(QObject):
    finished = Signal(int)


class Batch(QRunnable):
    def __init__(self, paths):
        super().__init__()
        self.paths = paths
        self.signals = Signals()

    def run(self):
        try:
            result = process_batch(self.paths)
        except Exception:
            result = 1
        self.signals.finished.emit(result)


class Droplet(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Fade Droplet')
        self.setMinimumSize(420, 180)
        self.setAcceptDrops(True)
        self.pending = []
        self.worker = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        layout = QVBoxLayout(self)
        self.label = QLabel('Перетащите видео сюда или на значок Fade Droplet.\nПараметры задаются в Fade Tools.')
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        button = QPushButton('Выбрать видео…')
        button.clicked.connect(self.choose)
        layout.addWidget(button)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.start)

    def choose(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Выбрать видео')
        for path in paths:
            self.add(path)

    def add(self, path):
        if path:
            self.pending.append(path)
            self.timer.start(250)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and all(url.isLocalFile() for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.add(url.toLocalFile())
        event.acceptProposedAction()

    def start(self):
        if self.worker or not self.pending:
            return
        paths, self.pending = self.pending, []
        self.label.setText(f'Обработка файлов: {len(paths)}. Можно добавить следующие файлы.')
        self.worker = Batch(paths)
        self.worker.signals.finished.connect(self.done)
        self.pool.start(self.worker)

    def done(self, result):
        self.worker = None
        self.label.setText('Готово.' if result == 0 else 'Обработка завершена с ошибками. Подробности в логах Fade Tools.')
        self.start()

    def closeEvent(self, event):
        if self.worker or self.pending:
            self.label.setText('Дождитесь завершения обработки перед закрытием.')
            event.ignore()
        else:
            event.accept()


def main():
    app = Application(sys.argv)
    window = Droplet()
    app.opened.connect(window.add)
    for path in sys.argv[1:]:
        if not path.startswith('-psn_'):
            window.add(path)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
