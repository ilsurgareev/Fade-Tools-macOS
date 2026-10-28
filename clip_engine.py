"""Checked clip encoding. A rendered preview is the export candidate itself."""
import hashlib
import logging
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from array import array
from bisect import bisect_left
from pathlib import Path

from clip_plan import clip_name, validate_range
from config import validate
from media import duration, encoding, inspect, pair_filters, probe, run, tool, encode_with_fallback


def visible_dimensions(video):
    """Apply the source container's clean aperture without any scaling."""
    width, height = int(video['width']), int(video['height'])
    crops = [s for s in video.get('side_data_list', []) if s.get('side_data_type') == 'Frame Cropping']
    if not crops:
        return width, height
    if len(crops) != 1:
        raise ValueError('Неоднозначные метаданные отображаемых границ видео')
    values = [crops[0].get('crop_' + side, 0) for side in ('left', 'right', 'top', 'bottom')]
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in values):
        raise ValueError('Некорректные метаданные отображаемых границ видео')
    left, right, top, bottom = values
    width, height = width - left - right, height - top - bottom
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError('Отображаемый размер видео должен быть положительным и чётным для H.265')
    return width, height


def export_signature(source, start, end):
    """Stable provenance for deciding whether an existing clip has the same range."""
    identity = f'{Path(source).resolve(strict=True)}\n{start:.9f}\n{end:.9f}'
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def matching_export(path, source, start, end):
    """Unmarked or unreadable files are never treated as our previous output."""
    try:
        return (Path(path).is_file() and
                probe(path).get('format', {}).get('tags', {}).get('fade_tools_range') ==
                export_signature(source, start, end))
    except (OSError, ValueError, RuntimeError, KeyError):
        return False


def range_timecode(info, fps, frame_offset):
    """Regenerate a known non-drop-frame SMPTE clock at the selected first frame."""
    tracks = [s for s in info['streams'] if s.get('codec_type') == 'data'
              and s.get('codec_tag_string') == 'tmcd']
    if len(tracks) > 1:
        raise ValueError('Несколько дорожек timecode: невозможно однозначно выбрать исходный отсчёт')
    values = {s.get('tags', {}).get('timecode') for s in info['streams']
              if s.get('codec_type') == 'video' or s in tracks}
    values.discard(None)
    values.discard('')
    if not values:
        if tracks:
            raise ValueError('Дорожка timecode не содержит читаемого исходного отсчёта')
        return None
    if len(values) != 1:
        raise ValueError('Timecode видеопотока и дорожки данных не совпадают')
    value = next(iter(values))
    match = re.fullmatch(r'(\d{2}):(\d{2}):(\d{2}):(\d{2})', value)
    nominal = round(fps)
    if not match or nominal not in (24, 25, 30, 50, 60) or min(
            abs(fps - nominal), abs(fps - nominal * 1000 / 1001)) > .001:
        raise ValueError('Этот формат timecode пока не поддерживается (нужен обычный HH:MM:SS:FF без drop-frame)')
    hour, minute, second, frame = map(int, match.groups())
    if hour >= 24 or minute >= 60 or second >= 60 or frame >= nominal:
        raise ValueError('Некорректный исходный timecode')
    absolute = ((hour * 3600 + minute * 60 + second) * nominal + frame + frame_offset)
    absolute %= 24 * 3600 * nominal
    whole, frame = divmod(absolute, nominal)
    hour, remainder = divmod(whole, 3600)
    minute, second = divmod(remainder, 60)
    return f'{hour:02d}:{minute:02d}:{second:02d}:{frame:02d}'


class PreviewCancelled(Exception):
    pass


class Cancellation:
    """Cancel only the child process owned by this preview, never the queue."""
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self._stage = ''
        self.fraction = None
        self.speed = ''
        self.started = time.monotonic()
        self.created = self.started
        self._overall = 0.0
        self._bounds = (0.0, .999)
        self._published = False

    @property
    def stage(self):
        return self._stage

    @stage.setter
    def stage(self, value):
        self.fraction = None
        self.speed = ''
        self.started = time.monotonic()
        self._stage = value

    def phase(self, stage, low, high):
        self.overall_fraction  # Retain the previous phase's observed progress.
        self.stage = stage
        self._bounds = (low, high)
        self._overall = max(self._overall, low)

    @property
    def overall_fraction(self):
        if self._published:
            return 1.0
        low, high = self._bounds
        part = max(0.0, min(1.0, self.fraction or 0.0))
        self._overall = min(.999, max(self._overall, low + (high-low)*part))
        return self._overall

    def published(self):
        # Called only after atomic publication has returned a saved path.
        self.check()
        self._published = True

    def render_status(self):
        elapsed = int(time.monotonic()-self.created)
        speed = f' · {self.speed}' if self.speed else ''
        return f'{int(self.overall_fraction*100)}% · {elapsed} с{speed}'

    def status_text(self):
        percent = '' if self.fraction is None else f' {int(self.fraction*100)}%'
        speed = f' · {self.speed}' if self.speed else ''
        elapsed = int(time.monotonic()-self.started)
        return f'{self.stage}{percent}{speed} · {elapsed} с'

    def run_progress(self, args, length):
        """Stream FFmpeg's own media-time progress, keeping diagnostics bounded."""
        self.check()
        last = {}
        command = list(args)
        command[1:1] = ['-progress', 'pipe:1', '-stats_period', '0.2', '-nostats']
        self.fraction = 0.0
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen([str(a) for a in command], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.bind(proc)
            try:
                for raw in proc.stdout:
                    key, separator, value = raw.decode('utf-8', errors='replace').strip().partition('=')
                    if not separator:
                        continue
                    last[key] = value
                    if key == 'out_time_us':
                        try:
                            self.fraction = max(0.0, min(.999, float(value)/1000000/length))
                        except ValueError:
                            pass
                    elif key == 'speed' and value != 'N/A':
                        self.speed = value.strip()
                proc.wait()
            finally:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                self.release(proc)
            self.check()
            errors.seek(0)
            diagnostic = errors.read().decode('utf-8', errors='replace')[-12000:]
            if proc.returncode:
                raise RuntimeError(diagnostic or f'Процесс завершился с кодом {proc.returncode}')
            if diagnostic:
                logging.warning('Tool diagnostics: %s', diagnostic)
        self.fraction = 1.0
        return last

    def check(self):
        if self.event.is_set():
            raise PreviewCancelled('Предпросмотр отменён')

    def bind(self, process):
        with self.lock:
            self.process = process
            if self.event.is_set() and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass

    def release(self, process):
        with self.lock:
            if self.process is process:
                self.process = None

    def cancel(self):
        with self.lock:
            self.event.set()
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.kill()
                except OSError:
                    pass

    def run(self, args, timeout=None):
        self.check()
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen([str(a) for a in args], stdin=subprocess.DEVNULL,
                stdout=output, stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.bind(proc)
            try:
                proc.wait(timeout=timeout)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                self.release(proc)
            self.check()
            errors.seek(0)
            diagnostic = errors.read().decode('utf-8', errors='replace')[-12000:]
            if proc.returncode:
                raise RuntimeError(diagnostic or f'Процесс завершился с кодом {proc.returncode}')
            if diagnostic:
                logging.warning('Tool diagnostics: %s', diagnostic)
            output.seek(0)
            return output.read()


def scan_timeline(source, fps, origin, cancellation=None, length=None):
    """Decode once and retain exact verified frame timestamps for subsequent previews."""
    timestamps = array('d')
    previous, expected, irregular = None, None, False
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen([tool('ffprobe'), '-v', 'error', '-threads', '0', '-select_streams', 'v:0',
            '-show_frames', '-show_streams', '-count_frames', '-show_entries',
            'frame=best_effort_timestamp_time:stream=nb_read_frames',
            '-of', 'compact=p=1:nk=0', str(source)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=errors,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if cancellation:
            cancellation.bind(proc)
        try:
            for line in proc.stdout:
                parts = line.decode('utf-8', errors='replace').strip().split('|')
                fields = dict(part.split('=', 1) for part in parts[1:] if '=' in part)
                if parts[0] == 'stream':
                    try:
                        expected = int(fields['nb_read_frames'])
                    except (KeyError, ValueError):
                        irregular = True
                    continue
                if parts[0] != 'frame':
                    continue
                try:
                    current = float(fields['best_effort_timestamp_time']) - origin
                except (KeyError, ValueError):
                    irregular = True
                    continue
                if not math.isfinite(current) or (previous is not None and
                        abs((current-previous) - 1/fps) > max(.001, .03/fps)):
                    irregular = True
                previous = current
                timestamps.append(current)
                if cancellation and length:
                    cancellation.fraction = max(0.0, min(.999, current/length))
            result_code = proc.wait()
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if cancellation:
                cancellation.release(proc)
        if cancellation:
            cancellation.check()
        if result_code:
            errors.seek(0)
            raise RuntimeError(errors.read().decode('utf-8', errors='replace')[-4000:])
    if len(timestamps) < 2 or expected != len(timestamps) or irregular:
        raise ValueError('Переменная или неопределённая частота кадров пока не поддерживается')
    if cancellation:
        cancellation.fraction = 1.0
    return timestamps


def source_fingerprint(source, state):
    return (str(source), state.st_size, state.st_mtime_ns, state.st_ctime_ns, state.st_ino)


def prepare(source, start, end, cfg, target, cancellation=None, analysis_cache=None):
    """Encode and validate into a caller-owned, non-existing temporary path.

    Only sources with synchronized tracks spanning the entire requested range
    are accepted. This deliberately refuses ambiguous partial-track cases.
    """
    source = Path(source).resolve(strict=True)
    target = Path(target)
    if not source.is_file() or target.exists() or source == target.resolve():
        raise ValueError('Некорректный исходный или временный файл')
    source_state = source.stat()
    validate(cfg)
    if cfg['codec'] != 'H.265':
        raise ValueError('Для отрывков поддерживается только H.265')
    cancellation = cancellation or Cancellation()
    execute = cancellation.run
    if cancellation:
        cancellation.phase('Читаем сведения о видео…', 0, .03)
    fingerprint = source_fingerprint(source, source_state)
    cached = dict(analysis_cache) if analysis_cache and analysis_cache.get('fingerprint') == fingerprint else None
    info = cached['info'] if cached else probe(source, cancellation=cancellation)
    video, audio, fps = inspect(info)
    visible_width, visible_height = visible_dimensions(video)
    pix = video.get('pix_fmt', '')
    if int(video.get('bits_per_raw_sample') or 0) > 10 or any(str(n) in pix for n in (12, 14, 16)):
        raise ValueError('Видео глубже 10 бит пока не поддерживается')
    if info.get('chapters'):
        raise ValueError('Файл содержит главы: их безопасная обрезка пока не поддерживается')
    range_timecode(info, fps, 0)  # Reject ambiguous or unsupported clocks before encoding.
    origin = float(video.get('start_time', 0))
    if abs(float(info.get('format', {}).get('start_time', 0)) - origin) > .001:
        raise ValueError('Начало видеодорожки не совпадает с началом файла')
    length = duration(video, info)
    validate_range(start, end, length, cfg, fps)
    for track in audio:
        offset = float(track.get('start_time', 0)) - origin
        if abs(offset) > 0.01 or duration(track, info) + 0.01 < end:
            raise ValueError('Аудиодорожка начинается или заканчивается вне диапазона; обработка отменена')
    # Until arbitrary VFR boundaries have been verified, refuse VFR and use
    # a frame grid. A nominal rate alone is not proof, so scan frame timestamps.
    if cancellation:
        cancellation.phase('Используем проверенные временные метки…' if cached else
                               'Проверяем временные метки всего исходника — один раз для файла…', .03, .65)
    timestamps = cached['timestamps'] if cached else scan_timeline(source, fps, origin, cancellation, length)
    if cancellation:
        cancellation.check()
    if source_fingerprint(source, source.stat()) != fingerprint:
        raise ValueError('Исходный файл изменился во время проверки')
    if analysis_cache is not None and not cached:
        analysis_cache.clear()
        analysis_cache.update(fingerprint=fingerprint, info=info, timestamps=timestamps)
    first_index = bisect_left(timestamps, start - 1e-7)
    last_index = bisect_left(timestamps, end - 1e-7)
    selected_count = last_index - first_index
    if not selected_count:
        raise ValueError('В выбранном диапазоне нет кадров')
    first = timestamps[first_index]
    # A non-frame-aligned start creates different video/audio origins; reject it.
    if abs(first - start) > 0.001 or abs(selected_count/fps - (end-start)) > 0.001:
        raise ValueError('Границы должны совпадать с границами кадров')
    clip_length = end - start
    timecode = range_timecode(info, fps, first_index)
    vf = pair_filters(cfg, 'video', fps, clip_length, 0, True, selected_count)
    af = pair_filters(cfg, 'audio', fps, clip_length, 0)
    args = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'warning', '-xerror', '-nostdin', '-n',
            '-ss', f'{start:.9f}', '-noautorotate', '-apply_cropping', 'all',
            '-i', source, '-t', f'{clip_length:.9f}',
            '-map', f'0:{video["index"]}']
    for track in audio:
        args += ['-map', f'0:{track["index"]}']
    args += ['-map_metadata', '0', '-map_chapters', '-1',
             '-metadata', 'fade_tools_range=' + export_signature(source, start, end)]
    args += ['-map_metadata:s:v:0', f'0:s:{video["index"]}']
    if timecode:
        args += ['-metadata:s:v:0', 'timecode=' + timecode,
                 '-metadata', 'timecode=' + timecode, '-write_tmcd', '1']
    if vf:
        args += ['-vf', ','.join(vf)]
    args += encoding(cfg, video) + ['-fps_mode:v', 'passthrough']
    for key, flag in (('color_range', '-color_range'), ('color_space', '-colorspace'),
                      ('color_transfer', '-color_trc'), ('color_primaries', '-color_primaries')):
        if video.get(key) not in (None, 'unknown', 'unspecified'):
            args += [flag, video[key]]
    for i, track in enumerate(audio):
        args += [f'-map_metadata:s:a:{i}', f'0:s:{track["index"]}']
        if af:
            args += [f'-filter:a:{i}', ','.join(af)]
        args += [f'-c:a:{i}', 'aac', f'-b:a:{i}', str(160000 * int(track['channels'])),
                 f'-ar:a:{i}', str(track['sample_rate'])]
        dispositions = '+'.join(k for k, val in track.get('disposition', {}).items() if val) or '0'
        args += [f'-disposition:a:{i}', dispositions]
    args += ['-movflags', '+use_metadata_tags', target]
    try:
        encode_with_fallback(args, cfg, video, target,
            lambda command: cancellation.run_progress(command, clip_length),
            lambda engine: cancellation.phase('Кодирование ' + engine, .65, .94))
        if cancellation:
            cancellation.phase('Проверяем готовый отрывок…', .94, .95)
        result = probe(target, count=False, cancellation=cancellation)
        if result.get('format', {}).get('tags', {}).get('fade_tools_range') != export_signature(source, start, end):
            raise ValueError('Проверка результата: метка диапазона не сохранена')
        out_v, out_a, _ = inspect(result)
        if timecode:
            clocks = [s for s in result['streams'] if s.get('codec_type') == 'data'
                      and s.get('codec_tag_string') == 'tmcd']
            if (len(clocks) != 1 or clocks[0].get('tags', {}).get('timecode') != timecode
                    or out_v.get('tags', {}).get('timecode') != timecode):
                raise ValueError('Проверка результата: timecode отрывка не сохранён или не совпал с началом')
        count = int(out_v.get('nb_read_frames') or out_v.get('nb_frames') or 0)
        if count != selected_count or len(out_a) != len(audio):
            raise ValueError('Проверка результата: число кадров или аудиодорожек изменилось')
        if abs(duration(out_v, result) - clip_length) > max(.05, 1/fps):
            raise ValueError('Проверка результата: неверная длительность видео')
        source_ten = int(video.get('bits_per_raw_sample') or 0) > 8 or '10' in video.get('pix_fmt', '')
        output_ten = int(out_v.get('bits_per_raw_sample') or 0) > 8 or '10' in out_v.get('pix_fmt', '')
        if source_ten != output_ten:
            raise ValueError('Проверка результата: изменилась разрядность видео')
        if (out_v['width'], out_v['height']) != (visible_width, visible_height):
            raise ValueError('Проверка результата: изменился отображаемый размер видео')
        if visible_dimensions(out_v) != (visible_width, visible_height):
            raise ValueError('Проверка результата: обрезка отображения применена повторно')
        for key in ('sample_aspect_ratio', 'color_range', 'color_space', 'color_transfer', 'color_primaries'):
            if video.get(key) not in (None, 'unknown', 'unspecified') and video.get(key) != out_v.get(key):
                raise ValueError(f'Проверка результата: изменилось {key}')
        if out_v.get('codec_name') != 'hevc':
            raise ValueError('Проверка результата: ожидается H.265')
        for before, after in zip(audio, out_a):
            for key in ('channels', 'channel_layout', 'sample_rate'):
                if before.get(key) != after.get(key):
                    raise ValueError(f'Проверка результата: изменилось {key}')
            for key in ('language', 'title'):
                value = before.get('tags', {}).get(key)
                if value and value != 'und' and value != after.get('tags', {}).get(key):
                    raise ValueError(f'Проверка результата: потеряно {key}')
            if abs(float(after.get('start_time', 0)) - float(out_v.get('start_time', 0))) > max(.01, 1/fps):
                raise ValueError('Проверка результата: нарушена синхронизация')
            if abs(duration(after, result) - clip_length) > max(.05, 1/fps):
                raise ValueError('Проверка результата: неверная длительность аудио')
        cancellation.phase('Полная проверка видео и звука', .95, .99)
        decoded = cancellation.run_progress([tool('ffmpeg'), '-v', 'error', '-xerror', '-nostdin',
             '-i', target, '-map', '0:v', '-map', '0:a?', '-f', 'null', '-'], clip_length)
        if int(decoded.get('frame', -1)) != selected_count:
            raise ValueError('Проверка результата: число декодированных кадров изменилось')
        after_state = source.stat()
        if source_fingerprint(source, after_state) != fingerprint:
            raise ValueError('Исходный файл изменился во время обработки')
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def publish(candidate, source, start, cfg, *, filename=None):
    source = Path(source).resolve(strict=True)
    candidate = Path(candidate).resolve(strict=True)
    directory = Path(cfg['output_dir']).expanduser() if cfg['output_dir'] else source.parent
    directory.mkdir(parents=True, exist_ok=True)
    base = Path(filename or clip_name(source, cfg['suffix'], start))
    if base.name != str(base) or '\\' in str(base) or base.suffix.lower() != '.mp4':
        raise ValueError('Некорректное имя результата')
    fd, temp_name = tempfile.mkstemp(prefix='.fade-clip-', suffix='.mp4', dir=directory)
    tmp = Path(temp_name)
    try:
        with candidate.open('rb') as inp, os.fdopen(fd, 'wb') as out:
            import shutil
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        with candidate.open('rb') as a, tmp.open('rb') as b:
            if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                raise ValueError('Проверка копии результата не пройдена')
        target = directory / base
        # Resolve symlinks and detect hard links before any replacement.
        if target.resolve() == source or (target.exists() and os.path.samefile(target, source)):
            raise ValueError('Нельзя перезаписать исходное видео')
        if target.exists() or target.is_symlink():
            candidate_tag = probe(candidate).get('format', {}).get('tags', {}).get('fade_tools_range')
            if not candidate_tag or candidate_tag != probe(target).get('format', {}).get('tags', {}).get('fade_tools_range'):
                raise FileExistsError('Существующий результат имеет другой или неизвестный диапазон')
            os.replace(tmp, target)
        else:
            os.link(tmp, target)
        return target
    finally:
        tmp.unlink(missing_ok=True)
