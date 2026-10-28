"""Interactive clip workspace; rendering never blocks the settings window."""
import copy
import json
import logging
import os
import tempfile
from bisect import bisect_left
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Qt, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QDesktopServices, QValidator, QIcon, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                               QFileDialog, QSlider, QDoubleSpinBox, QListWidget, QMessageBox,
                               QStyle, QStyleOptionSlider, QStyledItemDelegate, QGraphicsView, QGraphicsScene,
                               QFrame, QScrollArea, QGridLayout, QSizePolicy, QProgressBar)

from clip_engine import Cancellation, prepare, publish, visible_dimensions, matching_export
from live_fade import gain
from clip_plan import clip_name, validate_range
from frame_time import format_frame_index, format_frame_time, parse_frame_time
from media import duration, inspect, probe
from config import app_dir, validate


class NoticeLabel(QLabel):
    """Keep empty status space collapsed; errors remain visible."""
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))
        informational = ('Точный временный просмотр готов', 'Точный просмотр ·',
                         'Точный просмотр готов.', 'Готовим отрывок с Fade.')
        is_error = bool(text) and not text.startswith(informational)
        self.setStyleSheet('color: #ff6666;' if is_error else '')


class QueueItemDelegate(QStyledItemDelegate):
    progress_role = Qt.UserRole + 1

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 46)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        value = index.data(self.progress_role)
        if value is None:
            return
        margin = 8
        bar = QRect(option.rect.left() + margin, option.rect.bottom() - 9,
                    max(1, option.rect.width() - margin * 2), 4)
        fraction = max(0.0, min(1.0, float(value) / 1000))
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#303038'))
        painter.drawRoundedRect(bar, 2, 2)
        if fraction:
            filled = QRect(bar.left(), bar.top(), max(1, round(bar.width() * fraction)), bar.height())
            painter.setBrush(QColor('#4c75a0'))
            painter.drawRoundedRect(filled, 2, 2)
        painter.restore()


def white_media_icon(style, standard_icon):
    pixmap = style.standardIcon(standard_icon).pixmap(20, 20)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor('white'))
    painter.end()
    return QIcon(pixmap)


class Signals(QObject):
    done = Signal(object, object)


class Work(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = Signals()

    def run(self):
        try:
            self.signals.done.emit(self.operation(), None)
        except Exception as exc:
            self.signals.done.emit(None, str(exc))


class RangeSlider(QSlider):
    """Source timeline with independently draggable In, Out and playhead."""
    markersChanged = Signal(int, int)
    markersCommitted = Signal()

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 1000)
        self.selection = (self.minimum(), self.maximum())
        self.dragging_marker = None
        self.dragging_playhead = False
        self.setMinimumHeight(68)
        self.setAccessibleName('Шкала видео: перетащите In или Out, либо ползунок воспроизведения')
        palette = self.palette()
        highlight = palette.color(QPalette.Highlight)
        gray = round(.2126 * highlight.red() + .7152 * highlight.green() + .0722 * highlight.blue())
        palette.setColor(QPalette.Highlight, QColor(
            round((highlight.red() + gray) / 2),
            round((highlight.green() + gray) / 2),
            round((highlight.blue() + gray) / 2),
        ))
        self.setPalette(palette)

    def geometry_parts(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        return option, groove, max(1, groove.width() - handle.width()), groove.left() + handle.width() // 2

    def value_to_x(self, value):
        option, _, span, left = self.geometry_parts()
        return left + QStyle.sliderPositionFromValue(self.minimum(), self.maximum(),
            max(self.minimum(), min(self.maximum(), value)), span, option.upsideDown)

    def x_to_value(self, x):
        option, _, span, left = self.geometry_parts()
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
            max(0, min(span, x - left)), span, option.upsideDown)

    def groove_y(self):
        return self.geometry_parts()[1].center().y()

    def marker_rect(self, which):
        value = self.selection[0 if which == 'in' else 1]
        x = max(0, min(self.width() - 36, self.value_to_x(value) - 18))
        y = 4 if which == 'in' else self.height() - 26
        return QRect(x, y, 36, 22)

    def set_selection(self, start, end):
        self.selection = (start, end)
        self.setAccessibleDescription(f'In {start / 1000:.3f} с, Out {end / 1000:.3f} с')
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.maximum() <= self.minimum():
            return
        a, b = map(self.value_to_x, self.selection)
        y = self.groove_y()
        p = QPainter(self)
        p.setPen(QPen(QColor('#0a84ff'), 3))
        p.drawLine(a, y - 8, b, y - 8)
        for which, position, label in (('in', a, 'In'), ('out', b, 'Out')):
            rect = self.marker_rect(which)
            p.setPen(QPen(QColor('#0a84ff'), 1))
            p.setBrush(QColor('#163a5f'))
            p.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 3, 3)
            p.drawLine(position, rect.bottom() if which == 'in' else rect.top(), position, y)
            p.setPen(QColor('#edf1f7'))
            p.drawText(rect, Qt.AlignCenter, label)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.maximum() <= self.minimum():
            return super().mousePressEvent(event)
        for which in ('in', 'out'):
            if self.marker_rect(which).contains(event.position().toPoint()):
                self.dragging_marker = which
                self.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return
        self.dragging_playhead = True
        self.setSliderDown(True)
        self._move_playhead(event.position().toPoint().x())
        event.accept()

    def _move_playhead(self, x):
        value = self.x_to_value(x)
        self.setValue(value)

    def mouseMoveEvent(self, event):
        x = event.position().toPoint().x()
        if self.dragging_marker:
            value = self.x_to_value(x)
            start, end = self.selection
            if self.dragging_marker == 'in':
                start = min(value, end)
            else:
                end = max(value, start)
            self.set_selection(start, end)
            self.markersChanged.emit(start, end)
            event.accept()
        elif self.dragging_playhead:
            self._move_playhead(x)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging_marker:
            self.dragging_marker = None
            self.unsetCursor()
            self.markersCommitted.emit()
            event.accept()
        elif self.dragging_playhead:
            self._move_playhead(event.position().toPoint().x())
            self.dragging_playhead = False
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        before = self.value()
        super().keyPressEvent(event)
        if self.value() != before:
            self.sliderMoved.emit(self.value())


class FrameTimeSpinBox(QDoubleSpinBox):
    """Show elapsed minutes:seconds:frames while keeping seconds internally."""
    def __init__(self):
        self._fps = 25.0
        self._frame_boundaries = None
        super().__init__()
        self.setDecimals(9)
        self.setRange(0, 1000000)
        self.setKeyboardTracking(False)
        self.setToolTip('Минуты:секунды:кадры. Кадры считаются по частоте исходного видео.')

    def set_fps(self, fps):
        self._fps = fps
        self.lineEdit().setText(self.textFromValue(self.value()))

    def set_frame_boundaries(self, boundaries):
        self._frame_boundaries = boundaries
        self.lineEdit().setText(self.textFromValue(self.value()))

    def frame_index(self, value):
        if not self._frame_boundaries:
            return round(value * self._fps)
        index = bisect_left(self._frame_boundaries, value)
        if index == 0:
            return 0
        if index == len(self._frame_boundaries):
            return len(self._frame_boundaries) - 1
        before, after = self._frame_boundaries[index - 1:index + 1]
        return index - 1 if value - before <= after - value else index

    def textFromValue(self, value):
        if self._frame_boundaries:
            return format_frame_index(self.frame_index(value), self._fps)
        return format_frame_time(value, self._fps)

    def valueFromText(self, text):
        try:
            value = parse_frame_time(text, self._fps)
            if self._frame_boundaries:
                frame = max(0, min(len(self._frame_boundaries) - 1, round(value * self._fps)))
                return self._frame_boundaries[frame]
            return value
        except ValueError:
            return self.value()

    def validate(self, text, position):
        try:
            value = parse_frame_time(text, self._fps)
            state = QValidator.Acceptable if self.minimum() <= value <= self.maximum() else QValidator.Intermediate
        except ValueError:
            state = QValidator.Intermediate if all(c.isascii() and (c.isdigit() or c == ':') for c in text) else QValidator.Invalid
        return state, text, position

    def stepBy(self, steps):
        frame = self.frame_index(self.value()) + steps
        if self._frame_boundaries:
            frame = max(0, min(len(self._frame_boundaries) - 1, frame))
            self.setValue(self._frame_boundaries[frame])
        else:
            self.setValue(max(0, frame) / self._fps)


class LiveVideo(QGraphicsView):
    """Graphics video surface supports opacity over black without native overlays."""
    fileDropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsVideoItem()
        self.scene().addItem(self.item)
        self.setBackgroundBrush(QColor('black'))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.item.nativeSizeChanged.connect(self.fit_video)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.fileDropped.emit(urls[0].toLocalFile())
        else:
            event.ignore()

    def videoSink(self):
        return self.item.videoSink()

    def fit_video(self, *args):
        size = self.item.nativeSize()
        if size.width() > 0 and size.height() > 0:
            self.item.setSize(size)
            rect = QRectF(0, 0, size.width(), size.height())
            self.setSceneRect(rect)
            self.fitInView(rect, Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_video()


class ClipWindow(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.fps = 25.0
        self.frame_boundaries = None
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle('Fade Tools · Отрывки')
        self.resize(850, 720)
        self.setAcceptDrops(True)
        self.source = None
        self.info = None
        self.candidate = None
        self.candidates = set()
        self.queued_previews = {}
        self.playing_preview = None
        self.preview_mode = False
        self.live_mode = False
        self.live_snapshot = None
        self.job_tokens = []
        self.active_queue_job = None
        self.selected_render_only = False
        self.waiting_jobs = []
        self.queue_data = {}
        self.queue_load_error = None
        self.queue_paused = False
        self.queued_destinations = set()
        self.session_destinations = {}
        self.last_output = None
        self.last_render = None
        self.resume_preview = False
        self.preview_start = 0
        self.preview_end = 0
        self.pending_seek = None
        self.generation = 0
        self.active = 0
        self.preview_running = False
        self.preview_cancel = None
        self.analysis_cache = {}
        self.closing = False
        self.temp = tempfile.TemporaryDirectory(prefix='fade-tools-preview-', ignore_cleanup_errors=True)
        self.cleanup_attempt = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.pending = []
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.render_preview)
        self.preview_status_timer = QTimer(self)
        self.preview_status_timer.setInterval(250)
        self.preview_status_timer.timeout.connect(self.update_preview_status)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        open_button = QPushButton('Открыть видео…')
        open_button.clicked.connect(self.choose)
        top.addWidget(open_button)
        self.file_label = QLabel('Или перетащите видео в окно')
        self.file_label.setWordWrap(True)
        top.addWidget(self.file_label, 1)
        layout.addLayout(top)
        self.video = LiveVideo()
        self.video.fileDropped.connect(lambda path: self.open_file(path))
        self.video.setMinimumHeight(260)
        self.video.setStyleSheet('background:#000000;')
        layout.addWidget(self.video, 1)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video.item)
        self.player.errorOccurred.connect(lambda error, message: self.notice.setText('Ошибка воспроизведения: ' + message))
        self.video.videoSink().videoFrameChanged.connect(self.live_frame)
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(10)
        self.live_timer.timeout.connect(self.update_live_audio)
        self.live_timer.start()
        self.jobs_timer = QTimer(self)
        self.jobs_timer.setInterval(250)
        self.jobs_timer.timeout.connect(self.update_jobs)
        self.jobs_timer.start()
        self.player.tracksChanged.connect(self.select_audio)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.slider = RangeSlider()
        self.slider.setEnabled(False)
        self.slider.setToolTip('Перетащите In или Out; щёлкните по шкале для перемотки')
        self.slider.sliderMoved.connect(self.seek)
        self.slider.markersChanged.connect(self.markers_changed)
        self.slider.markersCommitted.connect(self.changed)
        layout.addWidget(self.slider)
        self.player.positionChanged.connect(self.position_changed)
        controls_content = QWidget()
        controls = QHBoxLayout(controls_content)
        controls.setContentsMargins(0, 0, 0, 0)
        self.play_button = QPushButton('Воспроизвести / Пауза')
        self.play_button.setToolTip('Воспроизведение / пауза: пробел')
        self.play_button.setIcon(white_media_icon(self.style(), QStyle.SP_MediaPlay))
        self.play_button.clicked.connect(self.play)
        self.player.playbackStateChanged.connect(self.playback_state_changed)
        controls.addWidget(self.play_button)
        original = QPushButton('Исходное видео')
        original.clicked.connect(self.show_original)
        controls.addWidget(original)
        self.position_label = QLabel('0:00:00 / 0:00:00')
        self.position_label.setObjectName('muted')
        controls.addWidget(self.position_label)
        controls.addStretch(1)
        for label in ('In', 'Out'):
            controls.addWidget(QLabel(label))
            spin = FrameTimeSpinBox()
            spin.setAlignment(Qt.AlignCenter)
            spin.setAccessibleName(label + ': минуты, секунды, кадры')
            spin.setMaximumWidth(120)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self.changed)
            setattr(self, label.lower(), spin)
            controls.addWidget(spin)
            mark = QPushButton(label)
            mark.setToolTip('Отметить ' + label + ' на текущей позиции')
            mark.clicked.connect(lambda checked=False, field=spin: field.setValue(
                self.player.position()/1000 + (self.preview_start if self.preview_mode else 0)))
            controls.addWidget(mark)
        controls_scroll = QScrollArea()
        controls_scroll.setFrameShape(QFrame.NoFrame)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        controls_scroll.setWidget(controls_content)
        controls_scroll.setFixedHeight(controls_content.sizeHint().height()
                                       + controls_scroll.horizontalScrollBar().sizeHint().height() + 2)
        layout.addWidget(controls_scroll)
        self.filename = QLabel('')
        self.filename.setWordWrap(True)
        self.filename.hide()
        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(6)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        actions.setColumnStretch(2, 1)
        self.preview_button = QPushButton('Отрывок с Fade')
        self.preview_button.setIcon(white_media_icon(self.style(), QStyle.SP_MediaPlay))
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.play_preview)
        actions.addWidget(self.preview_button, 0, 0)
        self.open_result = QPushButton('Открыть папку')
        self.open_result.setEnabled(False)
        self.open_result.clicked.connect(self.open_output_folder)
        actions.addWidget(self.open_result, 0, 1)
        queue = QPushButton('Добавить в очередь')
        self.queue = queue
        self.queue.setEnabled(False)
        self.queue.setToolTip('Добавить отрывок с текущими границами и настройками')
        queue.setObjectName('primary')
        queue.clicked.connect(self.enqueue)
        actions.addWidget(queue, 0, 2)
        layout.addLayout(actions)
        queue_controls = QGridLayout()
        queue_controls.setContentsMargins(0, 0, 0, 0)
        queue_controls.setHorizontalSpacing(6)
        queue_controls.setVerticalSpacing(6)
        queue_controls.setColumnStretch(0, 1)
        queue_controls.setColumnStretch(1, 1)
        queue_controls.setColumnStretch(2, 1)
        from settings_app import VisibleCheckBox
        self.auto_render = VisibleCheckBox('Рендерить сразу')
        self.auto_render.setToolTip('Начинать рендер при добавлении в очередь')
        self.auto_render.toggled.connect(lambda enabled: self.start_queue() if enabled else None)
        queue_controls.addWidget(self.auto_render, 1, 0)
        self.remove_job = QPushButton('Удалить из очереди')
        self.remove_job.setEnabled(False)
        self.remove_job.clicked.connect(self.remove_waiting_job)
        self.clear_queue = QPushButton('Очистить очередь')
        self.clear_queue.setToolTip('Удалить все ожидающие задания; текущий рендер продолжится')
        self.clear_queue.setEnabled(False)
        self.clear_queue.clicked.connect(self.clear_waiting_jobs)
        self.clear_queue.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.clear_queue.setMinimumWidth(0)
        self.render_queue = QPushButton('Рендер очереди')
        self.render_queue.setEnabled(False)
        self.render_queue.clicked.connect(self.queue_button_clicked)
        self.render_queue.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.remove_job.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.remove_job.setMinimumWidth(0)
        self.render_selected = QPushButton('Рендер')
        self.render_selected.setEnabled(False)
        self.render_selected.setToolTip('Рендерить только выбранное задание')
        self.render_selected.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.render_selected.setMinimumWidth(0)
        self.render_selected.clicked.connect(self.render_selected_job)
        self.render_queue.setMinimumWidth(0)
        left_queue_pair = QHBoxLayout()
        left_queue_pair.setContentsMargins(0, 0, 0, 0)
        left_queue_pair.setSpacing(6)
        left_queue_pair.addWidget(self.clear_queue, 1)
        left_queue_pair.addWidget(self.render_selected, 1)
        queue_controls.addLayout(left_queue_pair, 1, 1)
        render_pair = QHBoxLayout()
        render_pair.setContentsMargins(0, 0, 0, 0)
        render_pair.setSpacing(6)
        render_pair.addWidget(self.remove_job, 1)
        render_pair.addWidget(self.render_queue, 1)
        queue_controls.addLayout(render_pair, 1, 2)
        layout.addLayout(queue_controls)
        self.queue_controls = queue_controls
        self.processing = QLabel('')
        self.processing.setWordWrap(True)
        self.processing.hide()
        layout.addWidget(self.processing)
        self.saved_label = QLabel('')
        self.saved_label.hide()
        self.jobs = QListWidget()
        self.jobs.setObjectName('clipJobList')
        self.jobs.setAlternatingRowColors(True)
        self.jobs.setStyleSheet('''
            QListWidget#clipJobList {
                background: #1c1c20;
                alternate-background-color: #222328;
            }
            QListWidget#clipJobList::item {
                padding: 3px 6px;
                border-bottom: 1px solid #25262b;
            }
        ''')
        self.jobs.setMinimumHeight(110)
        self.jobs.setMaximumHeight(184)
        self.jobs.setItemDelegate(QueueItemDelegate(self.jobs))
        self.jobs.hide()
        self.jobs.itemDoubleClicked.connect(self.open_job_folder)
        self.jobs.currentItemChanged.connect(lambda *args: self.update_queue_controls())
        layout.addWidget(self.jobs)
        self.notice = NoticeLabel('')
        self.notice.hide()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        for on, value, unit in settings.rows.values():
            on.toggled.connect(self.changed)
            value.valueChanged.connect(self.changed)
            unit.currentIndexChanged.connect(self.changed)
        settings.folder.textChanged.connect(self.changed)
        settings.suffix.textChanged.connect(self.changed)
        settings.engine.currentIndexChanged.connect(self.changed)
        settings.bitrate.valueChanged.connect(self.changed)
        self.restore_queue()

    def save_queue(self):
        if self.queue_load_error:
            raise ValueError(self.queue_load_error)
        root = app_dir()
        root.mkdir(parents=True, exist_ok=True)
        jobs = ([self.active_queue_job] if self.active_queue_job else []) + self.waiting_jobs
        data = [self.queue_data[job[0][2]] for job in jobs]
        fd, name = tempfile.mkstemp(prefix='queue-', suffix='.tmp', dir=root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'jobs': data}, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, root / 'clip_queue.json')
        finally:
            Path(name).unlink(missing_ok=True)

    def restore_queue(self):
        path = app_dir() / 'clip_queue.json'
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data['version'] != 1 or not isinstance(data['jobs'], list):
                raise ValueError('Неподдерживаемый формат очереди')
            for entry in data['jobs']:
                validate(entry['config'])
                if not isinstance(entry['source'], str) or not isinstance(entry['label'], str) or Path(entry['label']).name != entry['label']:
                    raise ValueError('Некорректное задание очереди')
                if not (0 <= float(entry['start']) < float(entry['end'])):
                    raise ValueError('Некорректные границы очереди')
                if 'fps' not in entry:
                    try:
                        _, _, entry['fps'] = inspect(probe(entry['source']))
                    except Exception:
                        entry['fps'] = self.fps
                entry['range_label'] = entry.get('range_label') or (
                    f"{format_frame_time(float(entry['start']), float(entry['fps']))}–"
                    f"{format_frame_time(float(entry['end']), float(entry['fps']))}")
            for entry in data['jobs']:
                self.enqueue(entry)
        except Exception as exc:
            self.queue_load_error = str(exc)
            self.notice.setText('Не удалось восстановить очередь: ' + str(exc))

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
        else:
            event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.open_file(urls[0].toLocalFile())
        else:
            event.ignore()

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Открыть видео')
        if path:
            self.open_file(path)

    def open_file(self, path):
        try:
            info = probe(path)
            video, audio, fps = inspect(info)
            self.source = Path(path).resolve(strict=True)
            self.analysis_cache.clear()
            self.frame_boundaries = None
            self.info = info
            self.fps = fps
            self.in_spin().set_fps(fps)
            self.out.set_fps(fps)
            self.in_spin().set_frame_boundaries(None)
            self.out.set_frame_boundaries(None)
            self.audio_tracks = len(audio)
            displayed = visible_dimensions(video)
            self.crop_note = (f' · Видимая область исходника: {displayed[0]}×{displayed[1]}'
                              if displayed != (video['width'], video['height']) else '')
            self.file_label.setText(self.source.name)
            self.file_label.setToolTip(str(self.source) + self.crop_note)
            self.in_spin().blockSignals(True)
            self.out.blockSignals(True)
            self.in_spin().setValue(0)
            self.out.setValue(duration(video, info))
            self.in_spin().blockSignals(False)
            self.out.blockSignals(False)
            self.slider.setMaximum(round(duration(video, info) * 1000))
            self.slider.setEnabled(True)
            self.update_position_label(0)
            self.show_original()
            self.changed()
            self.timer.stop()
            self.notice.setText('')
        except Exception as exc:
            QMessageBox.warning(self, 'Не удалось открыть видео', str(exc))

    def in_spin(self):
        return getattr(self, 'in')

    def show_original(self, position=None, resume=False):
        if self.source:
            self.live_mode = False
            self.live_snapshot = None
            self.video.item.setOpacity(1)
            self.audio.setVolume(1)
            self.preview_mode = False
            self.resume_preview = False
            self.pending_seek = (position, resume) if position is not None else None
            url = QUrl.fromLocalFile(str(self.source))
            if self.player.source() != url:
                self.player.setSource(url)
            elif position is not None:
                self.seek(position)
                self.pending_seek = None
                if resume:
                    self.player.play()
            self.playing_preview = None
            self.cleanup_previews()

    def media_status_changed(self, status):
        if self.pending_seek is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            position, resume = self.pending_seek
            self.pending_seek = None
            self.player.setPosition(position)
            if resume:
                self.player.play()

    def select_audio(self):
        if self.player.audioTracks():
            self.player.setActiveAudioTrack(0)

    def play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def playback_state_changed(self, state):
        icon = QStyle.SP_MediaPause if state == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self.play_button.setIcon(white_media_icon(self.style(), icon))

    def position_changed(self, value):
        absolute = value + (round(self.preview_start * 1000) if self.preview_mode else 0)
        if self.pending_seek is None:
            self.update_position_label(absolute)
        if not self.slider.isSliderDown() and self.pending_seek is None:
            self.slider.setValue(self._frame_milliseconds(absolute / 1000))

    def time_label(self, milliseconds):
        seconds = max(0, milliseconds) / 1000
        return self.frame_label(seconds)

    def frame_label(self, seconds):
        if self.frame_boundaries:
            return format_frame_index(self._frame_index(seconds), self.fps)
        return format_frame_time(seconds, self.fps)

    def _frame_index(self, seconds):
        boundaries = self.frame_boundaries
        if not boundaries:
            return round(seconds * self.fps)
        index = bisect_left(boundaries, seconds)
        if index == 0:
            return 0
        if index == len(boundaries):
            return len(boundaries) - 1
        before, after = boundaries[index - 1:index + 1]
        return index - 1 if seconds - before <= after - seconds else index

    def _frame_time(self, seconds):
        if self.frame_boundaries:
            return self.frame_boundaries[self._frame_index(seconds)]
        return round(seconds * self.fps) / self.fps

    def _frame_milliseconds(self, seconds):
        return int(self._frame_time(seconds) * 1000 + .5)

    def update_position_label(self, position):
        self.position_label.setText(f'{self.time_label(position)} / {self.time_label(self.slider.maximum())}')

    def seek(self, value):
        value = self._frame_milliseconds(value / 1000)
        self.slider.setValue(value)
        self.update_position_label(value)
        if self.live_mode and self.live_snapshot:
            _, start, end, _ = self.live_snapshot
            if value < round(start*1000) or value >= round(end*1000):
                self.live_mode = False
                self.video.item.setOpacity(1)
                self.audio.setVolume(1)
        if self.preview_mode:
            start = round(self.preview_start * 1000)
            end = round(self.preview_end * 1000)
            if value < start or value > end:
                resume = self.player.playbackState() == QMediaPlayer.PlayingState
                self.show_original(value, resume)
                return
            value -= start
        if self.player.mediaStatus() in (QMediaPlayer.NoMedia, QMediaPlayer.LoadingMedia):
            resume = self.pending_seek[1] if self.pending_seek else self.player.playbackState() == QMediaPlayer.PlayingState
            self.pending_seek = (value, resume)
            return
        self.player.setPosition(value)

    def markers_changed(self, start, end):
        self.in_spin().blockSignals(True)
        self.out.blockSignals(True)
        self.in_spin().setValue(start / 1000)
        self.out.setValue(end / 1000)
        self.in_spin().blockSignals(False)
        self.out.blockSignals(False)
        self.changed()

    def changed(self, *args):
        if not self.source:
            return
        if self.preview_cancel:
            self.preview_cancel.cancel()
        self.resume_preview = False
        was_exact = self.preview_mode
        resume = self.player.playbackState() == QMediaPlayer.PlayingState
        absolute = self.player.position() + (round(self.preview_start*1000) if was_exact else 0)
        if was_exact:
            self.show_original(absolute, resume)
            self.live_mode = True
        self.generation += 1
        self.candidate = None
        self.queue.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.cleanup_previews()
        self.snapshot()  # Snap the editable In/Out fields to the source frame grid.
        self.slider.set_selection(self._frame_milliseconds(self.in_spin().value()),
                                  self._frame_milliseconds(self.out.value()))
        base = Path(clip_name(self.source, self.settings.suffix.text(), self.snapshot()[1]))
        directory = Path(self.settings.folder.text()).expanduser() if self.settings.folder.text() else self.source.parent
        self.queue.setToolTip('Добавить в очередь: ' + str(directory / base) + '\nПри совпадении имени добавятся начало и конец')
        self.filename.setText('Файл после сохранения: ' + str(directory / base) +
                              ' · При совпадении имени добавятся начало и конец')
        self.timer.stop()
        try:
            snapshot = self.snapshot()
            validate_range(snapshot[1], snapshot[2], self.slider.maximum()/1000,
                           snapshot[3], self.fps)
            self.live_snapshot = snapshot
            self.queue.setEnabled(True)
            self.preview_button.setEnabled(True)
            self.notice.setText('')
            self.refresh_live_video()
            self.update_live_audio()
        except Exception as exc:
            self.live_snapshot = None
            self.live_mode = False
            self.video.item.setOpacity(1)
            self.audio.setVolume(1)
            self.notice.setText(str(exc))

    def snapshot(self):
        # Prefer verified source PTS boundaries; nominal FPS is only a fallback.
        start = self._frame_time(self.in_spin().value())
        end = self._frame_time(self.out.value())
        self.in_spin().blockSignals(True)
        self.out.blockSignals(True)
        self.in_spin().setValue(start)
        self.out.setValue(end)
        self.in_spin().blockSignals(False)
        self.out.blockSignals(False)
        return self.source, start, end, copy.deepcopy(self.settings.data())

    def _apply_cached_frame_grid(self, request):
        aligned = self.analysis_cache.get('aligned_range')
        if aligned is None or self.analysis_cache.get('alignment_request') != tuple(request):
            return tuple(request)
        timestamps = self.analysis_cache.get('timestamps')
        if timestamps:
            end = self.slider.maximum() / 1000
            boundaries = list(timestamps)
            if end > boundaries[-1] + 1e-9:
                boundaries.append(end)
            self.frame_boundaries = boundaries
            self.in_spin().set_frame_boundaries(boundaries)
            self.out.set_frame_boundaries(boundaries)
        start, end = aligned
        if (abs(self.in_spin().value() - request[0]) < 1e-6 and
                abs(self.out.value() - request[1]) < 1e-6):
            self.in_spin().blockSignals(True)
            self.out.blockSignals(True)
            self.in_spin().setValue(start)
            self.out.setValue(end)
            self.in_spin().blockSignals(False)
            self.out.blockSignals(False)
            self.slider.set_selection(self._frame_milliseconds(start), self._frame_milliseconds(end))
            self.live_snapshot = (self.source, start, end, copy.deepcopy(self.settings.data()))
        return self.source, start, end, self.settings.data()

    def launch(self, operation, callback):
        work = Work(operation)
        self.pending.append(work)
        self.active += 1
        def finish(result, error):
            self.pending.remove(work)
            self.active -= 1
            callback(result, error)
            if self.closing and not self.active:
                self.finish_close()
        work.signals.done.connect(finish, Qt.QueuedConnection)
        self.pool.start(work)

    def render_preview(self):
        if not self.source or self.preview_running:
            return
        self.preview_running = True
        cancellation = Cancellation()
        self.preview_cancel = cancellation
        self.preview_status_timer.start()
        snapshot = self.snapshot()
        generation = self.generation
        target = Path(self.temp.name) / f'preview-{generation}.mp4'
        def done(result, error):
            self.preview_status_timer.stop()
            self.preview_running = False
            self.preview_cancel = None
            if self.closing:
                if result:
                    Path(result).unlink(missing_ok=True)
                return
            if generation != self.generation:
                if result:
                    Path(result).unlink(missing_ok=True)
                if self.resume_preview:
                    self.timer.start(0)
                return
            if error:
                self.resume_preview = False
                self.notice.setText('Ошибка предпросмотра: ' + error)
            else:
                aligned_snapshot = self._apply_cached_frame_grid(snapshot[1:3])
                try:
                    self.candidate_source_state = snapshot[0].stat()
                except OSError as exc:
                    Path(result).unlink(missing_ok=True)
                    self.notice.setText('Исходный файл недоступен: ' + str(exc))
                    return
                self.candidate = result
                self.candidates.add(Path(result))
                self.queue.setEnabled(True)
                suffix = ' · звучит первая из %d аудиодорожек' % self.audio_tracks if self.audio_tracks > 1 else ''
                self.notice.setText('Точный временный просмотр готов · для сохранения нажмите «Добавить в очередь» · In %s · Out %s%s%s' %
                                    (self.frame_label(aligned_snapshot[1]),
                                     self.frame_label(aligned_snapshot[2]), suffix, self.crop_note))
                if self.resume_preview:
                    self.resume_preview = False
                    self.play_exact()
                self.cleanup_previews()
        self.launch(lambda: prepare(*snapshot, target, cancellation=cancellation,
                                    analysis_cache=self.analysis_cache), done)

    def update_preview_status(self):
        token = self.preview_cancel
        if token and token.stage and not token.event.is_set():
            suffix = ' Затем начнётся воспроизведение.' if self.resume_preview else ''
            self.notice.setText('Точный просмотр · ' + token.status_text() + suffix)

    def play_exact(self):
        self.live_mode = False
        self.video.item.setOpacity(1)
        self.audio.setVolume(1)
        if self.candidate:
            self.notice.setText('Точный просмотр готов. Файл временный — нажмите «Добавить в очередь», чтобы оставить его в папке.')
            self.preview_mode = True
            self.preview_start = self.snapshot()[1]
            self.preview_end = self.snapshot()[2]
            self.pending_seek = None
            self.player.setSource(QUrl.fromLocalFile(str(self.candidate)))
            self.playing_preview = Path(self.candidate)
            self.player.play()
            self.cleanup_previews()
        else:
            if self.source:
                self.resume_preview = True
                self.notice.setText('Готовим отрывок с Fade. Воспроизведение начнётся автоматически.')
                if not self.preview_running:
                    self.timer.start(0)

    def play_preview(self):
        if not self.source or not self.live_snapshot:
            return
        snapshot = self.snapshot()
        self.resume_preview = False
        if self.preview_cancel:
            self.preview_cancel.cancel()
        self.timer.stop()
        if self.preview_mode:
            self.show_original(round(snapshot[1]*1000), True)
        else:
            self.seek(round(snapshot[1]*1000))
        self.live_snapshot = snapshot
        self.live_mode = True
        self.refresh_live_video()
        self.update_live_audio()
        self.player.play()
        self.notice.setText('')

    def live_frame(self, frame):
        if self.live_mode and self.live_snapshot and frame.isValid():
            # Decoder frame timestamp avoids applying the video envelope to a coarse UI clock.
            video = next(s for s in self.info['streams'] if s.get('codec_type') == 'video')
            position = frame.startTime()/1000000 - float(video.get('start_time', 0))
            if frame.startTime() < 0:
                position = self.player.position()/1000
            self.apply_video_gain(position)

    def apply_video_gain(self, position):
        if self.live_mode and self.live_snapshot:
            _, start, end, cfg = self.live_snapshot
            self.video.item.setOpacity(gain(cfg, 'video', position-start, end-start, self.fps))

    def refresh_live_video(self):
        frame = self.video.videoSink().videoFrame()
        if frame.isValid():
            self.live_frame(frame)
        else:
            self.apply_video_gain(self.player.position()/1000)

    def update_live_audio(self):
        if self.live_mode and self.live_snapshot:
            _, start, end, cfg = self.live_snapshot
            position = self.player.position()/1000
            self.audio.setVolume(gain(cfg, 'audio', position-start, end-start, self.fps))
            if position >= end:
                self.player.pause()
                self.audio.setVolume(0)

    def update_jobs(self):
        for item, label, token in self.job_tokens:
            if token.stage:
                range_label = self.queue_data.get(token, {}).get('range_label', '')
                suffix = (' · ' + range_label) if range_label else ''
                item.setText(label + suffix + ' · рендер ' + token.render_status())
                item.setData(QueueItemDelegate.progress_role, int(token.overall_fraction * 1000))
            else:
                item.setData(QueueItemDelegate.progress_role, None)
            self.jobs.viewport().update(self.jobs.visualItemRect(item))

        tokens = ([self.preview_cancel] if self.preview_cancel else []) + [r[2] for r in self.job_tokens]
        token = next((t for t in tokens if t.process is not None), tokens[0] if tokens else None)
        if self.job_tokens:
            # Queue progress belongs to each job row; avoid a duplicate overall indicator.
            self.processing.hide()
        elif token and token is self.preview_cancel:
            label = 'Предпросмотр'
            self.processing.setText(label + ' · ' + token.render_status())
            self.processing.show()
        elif self.last_render:
            label, error, fraction = self.last_render
            self.processing.setText(label + (' · ошибка сохранения' if error else ' · готово'))
            self.processing.show()
        else:
            self.processing.hide()
        active_queue = bool(self.active_queue_job)
        self.render_queue.setText('Стоп' if active_queue else 'Рендер очереди')
        self.render_queue.setToolTip('Остановить текущий рендер; остальные задания останутся в очереди'
                                     if active_queue else '')
        self.render_queue.setEnabled(active_queue or bool(self.waiting_jobs))
        self.clear_queue.setEnabled(bool(self.waiting_jobs) and not self.queue_load_error)

    def open_output_folder(self):
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output.parent)))

    def open_job_folder(self, item):
        path = item.data(Qt.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def enqueue(self, restored=None):
        if isinstance(restored, bool):
            restored = None  # QPushButton.clicked supplies its checked state.
        if restored is None and (not self.source or not self.live_snapshot):
            return
        if restored:
            fps = float(restored.get('fps', self.fps))
            start = round(float(restored['start']) * fps) / fps
            end = round(float(restored['end']) * fps) / fps
            if not 0 <= start < end:
                raise ValueError('После привязки сохранённого задания к кадрам диапазон пуст')
            snapshot = (Path(restored['source']), start, end, restored['config'])
        else:
            snapshot = self.snapshot()
        preview = None if restored else self.candidate
        try:
            source_state = None if restored else (self.candidate_source_state if preview else snapshot[0].stat())
            directory = Path(snapshot[3]['output_dir']).expanduser() if snapshot[3]['output_dir'] else snapshot[0].parent
            label = clip_name(snapshot[0], snapshot[3]['suffix'], snapshot[1])
            if restored:
                label = restored['label']
            destination = os.path.normcase(str((directory / label).resolve()))
            identity = (os.path.normcase(str(snapshot[0])), round(snapshot[1], 9), round(snapshot[2], 9))
            if not restored and (destination in self.queued_destinations or
                    ((directory / label).exists() and self.session_destinations.get(destination) != identity
                     and not matching_export(directory / label, snapshot[0], snapshot[1], snapshot[2])) or
                    (destination in self.session_destinations and self.session_destinations[destination] != identity)):
                label = clip_name(snapshot[0], snapshot[3]['suffix'], snapshot[1], snapshot[2])
                destination = os.path.normcase(str((directory / label).resolve()))
                number = 2
                while destination in self.queued_destinations or (directory / label).exists():
                    label = clip_name(snapshot[0], snapshot[3]['suffix'], snapshot[1], snapshot[2], number)
                    destination = os.path.normcase(str((directory / label).resolve()))
                    number += 1
            output_path = directory / label
            previous_output = output_path.stat() if output_path.exists() else None
            def fingerprint(state):
                return None if state is None else [state.st_size, state.st_mtime_ns, state.st_ctime_ns, state.st_ino]
            source_fingerprint = restored['source_fingerprint'] if restored else fingerprint(source_state)
            output_fingerprint = restored['output_fingerprint'] if restored else fingerprint(previous_output)
            fd, name = tempfile.mkstemp(prefix='queued-', suffix='.mp4', dir=self.temp.name)
            os.close(fd)
            target = Path(name)
            target.unlink()
        except OSError as exc:
            if restored:
                raise
            self.notice.setText('Не удалось добавить в очередь: ' + str(exc))
            return
        self.queued_destinations.add(destination)
        if preview:
            self.queued_previews[preview] = self.queued_previews.get(preview, 0) + 1
        fps = float(restored.get('fps', self.fps)) if restored else self.fps
        range_label = f'{self.frame_label(snapshot[1])}–{self.frame_label(snapshot[2])}'
        self.jobs.addItem(label + ' · ' + range_label + ' · в очереди')
        self.jobs.show()
        item = self.jobs.item(self.jobs.count()-1)
        token = Cancellation()
        record = [item, label, token]
        original_range = snapshot[1:3]
        def operation():
            nonlocal snapshot, label, destination, identity, output_path, output_fingerprint, range_label
            current = snapshot[0].stat()
            if fingerprint(current) != source_fingerprint:
                raise ValueError('Исходный файл изменился после добавления в очередь')
            requested_range = original_range
            candidate = preview or prepare(*snapshot, target, cancellation=token,
                                           analysis_cache=self.analysis_cache)
            try:
                aligned = self.analysis_cache.get('aligned_range')
                if self.analysis_cache.get('alignment_request') == requested_range and aligned:
                    aligned_start, aligned_end = aligned
                    if (aligned_start, aligned_end) != requested_range:
                        snapshot = (snapshot[0], aligned_start, aligned_end, snapshot[3])
                        range_label = (f'{self.frame_label(aligned_start)}–'
                                       f'{self.frame_label(aligned_end)}')
                        identity = (os.path.normcase(str(snapshot[0])),
                                    round(aligned_start, 9), round(aligned_end, 9))
                        desired_label = clip_name(snapshot[0], snapshot[3]['suffix'], aligned_start)
                        desired_path = directory / desired_label
                        desired_destination = os.path.normcase(str(desired_path.resolve()))
                        if ((desired_destination != destination and
                             desired_destination in self.queued_destinations) or desired_path.exists()):
                            desired_label = clip_name(snapshot[0], snapshot[3]['suffix'],
                                                      aligned_start, aligned_end)
                            desired_path = directory / desired_label
                            desired_destination = os.path.normcase(str(desired_path.resolve()))
                            number = 2
                            while (desired_destination in self.queued_destinations or
                                   desired_path.exists()):
                                desired_label = clip_name(snapshot[0], snapshot[3]['suffix'],
                                                          aligned_start, aligned_end, number)
                                desired_path = directory / desired_label
                                desired_destination = os.path.normcase(str(desired_path.resolve()))
                                number += 1
                        if desired_destination != destination:
                            self.queued_destinations.discard(destination)
                            self.queued_destinations.add(desired_destination)
                            destination = desired_destination
                            output_path = desired_path
                            output_fingerprint = None
                        label = desired_label
                        record[1] = label
                        self.queue_data[token].update(
                            start=aligned_start, end=aligned_end, label=label,
                            range_label=range_label, output_fingerprint=output_fingerprint)
                token.phase('Сохраняем проверенный отрывок…', .99, .999)
                current_output = output_path.stat() if output_path.exists() else None
                if output_fingerprint != fingerprint(current_output):
                    raise ValueError('Файл результата изменился во время ожидания; сохранение отменено')
                default_label = clip_name(snapshot[0], snapshot[3]['suffix'], snapshot[1])
                result = (publish(candidate, snapshot[0], snapshot[1], snapshot[3]) if label == default_label
                          else publish(candidate, snapshot[0], snapshot[1], snapshot[3], filename=label))
                token.published()
                return result
            finally:
                target.unlink(missing_ok=True)
        def done(result, error):
            self._apply_cached_frame_grid(original_range)
            self.last_render = (label, error, token.overall_fraction)
            self.job_tokens.remove(record)
            item.setData(QueueItemDelegate.progress_role,
                         int(token.overall_fraction * 1000) if error else 1000)
            if error and token.event.is_set():
                self.queue_paused = True
            self.queued_destinations.discard(destination)
            if preview:
                self.queued_previews[preview] -= 1
                if not self.queued_previews[preview]:
                    del self.queued_previews[preview]
            self.cleanup_previews()
            item.setText(label + ' · ' + range_label +
                         (' · ошибка: ' + error if error else ' · готово: ' + str(result)))
            if not error:
                self.session_destinations[destination] = identity
                self.last_output = Path(result)
                self.saved_label.setText('Сохранено: ' + str(result))
                self.open_result.setEnabled(True)
                self.open_result.setToolTip(str(Path(result).parent))
                item.setData(Qt.UserRole, str(result))
                item.setToolTip('Сохранённый файл: ' + str(result) + '\nДвойной щелчок — открыть папку')
            else:
                self.saved_label.setText('Не сохранено: ' + error)
                self.notice.setText('Не сохранено: ' + error)
            self.last_render = (label, error, token.overall_fraction)
            self.update_jobs()
            self.update_queue_controls()
        self.waiting_jobs.append((record, operation, done, preview, target, destination))
        self.queue_data[token] = {'source': str(snapshot[0]), 'start': snapshot[1], 'end': snapshot[2],
                                  'fps': fps, 'range_label': range_label,
                                  'config': snapshot[3], 'label': label,
                                  'source_fingerprint': source_fingerprint, 'output_fingerprint': output_fingerprint}
        if not restored:
            try:
                if self.queue_load_error:
                    raise ValueError(self.queue_load_error)
                self.save_queue()
            except (OSError, ValueError) as exc:
                self.waiting_jobs.pop()
                self.queue_data.pop(token)
                self.queued_destinations.discard(destination)
                if preview:
                    self.queued_previews[preview] -= 1
                    if not self.queued_previews[preview]:
                        del self.queued_previews[preview]
                self.jobs.takeItem(self.jobs.row(item))
                self.notice.setText('Не удалось сохранить очередь: ' + str(exc))
                self.update_queue_controls()
                return
        self.notice.setText('')
        self.update_queue_controls()
        if not restored and self.auto_render.isChecked():
            self.start_queue()

    def update_queue_controls(self):
        active_queue = bool(self.active_queue_job)
        self.clear_queue.setEnabled(bool(self.waiting_jobs) and not self.queue_load_error)
        self.render_queue.setText('Стоп' if active_queue else 'Рендер очереди')
        self.render_queue.setToolTip('Остановить текущий рендер; остальные задания останутся в очереди'
                                     if active_queue else '')
        self.render_queue.setEnabled(active_queue or bool(self.waiting_jobs))
        selected = self.jobs.currentItem()
        selected_waiting = any(job[0][0] is selected for job in self.waiting_jobs)
        self.remove_job.setEnabled(selected_waiting and not active_queue)
        self.render_selected.setEnabled(selected_waiting and not active_queue and not self.queue_load_error)

    def start_queue(self):
        if self.queue_load_error:
            self.notice.setText('Не удалось восстановить очередь: ' + self.queue_load_error)
            return
        if self.active_queue_job:
            return
        if not self.waiting_jobs:
            return
        self.queue_paused = False
        self.selected_render_only = False
        self.start_next_queue_job()

    def render_selected_job(self):
        if self.active_queue_job or self.queue_load_error:
            return
        selected = self.jobs.currentItem()
        for index, job in enumerate(self.waiting_jobs):
            if job[0][0] is selected:
                self.waiting_jobs.pop(index)
                self.waiting_jobs.insert(0, job)
                try:
                    self.save_queue()
                except (OSError, ValueError) as exc:
                    self.waiting_jobs.pop(0)
                    self.waiting_jobs.insert(index, job)
                    self.notice.setText('Не удалось сохранить очередь: ' + str(exc))
                    self.update_queue_controls()
                    return
                self.queue_paused = False
                self.selected_render_only = True
                self.start_next_queue_job()
                return

    def start_next_queue_job(self):
        if self.active_queue_job or self.queue_paused or not self.waiting_jobs:
            return
        job = self.waiting_jobs.pop(0)
        record, operation, done = job[:3]
        self.job_tokens.append(record)
        self.active_queue_job = job

        def finished(result, error):
            done(result, error)
            self.active_queue_job = None
            continue_queue = not self.selected_render_only
            self.selected_render_only = False
            try:
                self.save_queue()
                self.queue_data.pop(record[2], None)
            except (OSError, ValueError) as exc:
                self.queue_paused = True
                self.queue_load_error = str(exc)
                self.notice.setText('Не удалось сохранить очередь: ' + str(exc))
            self.update_queue_controls()
            if continue_queue and self.waiting_jobs and not self.queue_paused:
                self.start_next_queue_job()

        self.launch(operation, finished)
        self.update_queue_controls()

    def queue_button_clicked(self):
        if self.active_queue_job:
            self.stop_queue()
            return
        self.start_queue()

    def stop_queue(self):
        if not self.active_queue_job:
            return
        self.queue_paused = True
        record, operation, done, preview, target, destination = self.active_queue_job
        token = record[2]
        if token is not None:
            token.cancel()
        # Nothing else is submitted to the worker pool; queued jobs remain intact.

    def remove_waiting_job(self):
        selected = self.jobs.currentItem()
        for job in self.waiting_jobs:
            record, _, _, preview, target, destination = job
            if record[0] is selected:
                self.waiting_jobs.remove(job)
                try:
                    self.save_queue()
                except (OSError, ValueError) as exc:
                    self.waiting_jobs.append(job)
                    self.notice.setText('Не удалось сохранить очередь: ' + str(exc))
                    break
                self.queue_data.pop(record[2], None)
                self.queued_destinations.discard(destination)
                if preview:
                    self.queued_previews[preview] -= 1
                    if not self.queued_previews[preview]:
                        del self.queued_previews[preview]
                target.unlink(missing_ok=True)
                self.jobs.takeItem(self.jobs.row(selected))
                self.jobs.setVisible(self.jobs.count() > 0)
                self.cleanup_previews()
                break
        self.update_queue_controls()

    def clear_waiting_jobs(self):
        if not self.waiting_jobs:
            return
        waiting = self.waiting_jobs
        self.waiting_jobs = []
        try:
            self.save_queue()
        except (OSError, ValueError) as exc:
            self.waiting_jobs = waiting
            self.notice.setText('Не удалось сохранить очередь: ' + str(exc))
            self.update_queue_controls()
            return
        for record, _, _, preview, target, destination in waiting:
            self.queue_data.pop(record[2], None)
            self.queued_destinations.discard(destination)
            if preview:
                self.queued_previews[preview] -= 1
                if not self.queued_previews[preview]:
                    del self.queued_previews[preview]
            target.unlink(missing_ok=True)
            self.jobs.takeItem(self.jobs.row(record[0]))
        self.jobs.setVisible(self.jobs.count() > 0)
        self.cleanup_previews()
        self.update_queue_controls()

    def cleanup_previews(self):
        protected = set(self.queued_previews)
        if self.candidate:
            protected.add(Path(self.candidate))
        if self.playing_preview:
            protected.add(self.playing_preview)
        for path in self.candidates - protected:
            try:
                path.unlink(missing_ok=True)
                self.candidates.discard(path)
            except OSError:
                pass  # Windows may still be releasing a player handle; retry later.

    def closeEvent(self, event):
        if self.job_tokens:
            QMessageBox.information(self, 'Очередь', 'Сначала выполните задания или удалите ожидающие из очереди.')
            event.ignore()
            return
        self.timer.stop()
        self.live_timer.stop()
        self.preview_status_timer.stop()
        self.closing = True
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        if self.preview_cancel:
            self.preview_cancel.cancel()
        self.player.pause()
        if not hasattr(self.settings, 'detached_clips'):
            self.settings.detached_clips = []
        if self not in self.settings.detached_clips:
            self.settings.detached_clips.append(self)
        self.settings.clip_window = None
        if not self.active:
            self.finish_close()
        event.accept()

    def finish_close(self):
        if self.player.mediaStatus() == QMediaPlayer.LoadingMedia:
            QTimer.singleShot(100, self.finish_close)
            return
        if self.cleanup_attempt == 0:
            self.player.stop()
            self.player.setSource(QUrl())
        try:
            self.temp.cleanup()
        except OSError as exc:
            # Windows may still hold a player/file handle; retry without letting
            # cleanup failure escape the window's close event.
            logging.warning('Не удалось удалить временный предпросмотр: %s', exc)
        if Path(self.temp.name).exists() and self.cleanup_attempt < 20:
            self.cleanup_attempt += 1
            QTimer.singleShot(250, self.finish_close)
            return
        if Path(self.temp.name).exists():
            logging.warning('Не удалось удалить временный предпросмотр: %s', self.temp.name)
        if getattr(self.settings, 'clip_window', None) is self:
            self.settings.clip_window = None
        if self in getattr(self.settings, 'detached_clips', []):
            self.settings.detached_clips.remove(self)
        self.deleteLater()
