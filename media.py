"""FFmpeg processing with explicit mapping, validation and no-replace publication."""
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from config import seconds, validate


def base_dir():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent


def tool(name):
    override = os.environ.get('FADE_TOOLS_' + name.upper())
    bundled = base_dir() / 'tools' / (name + ('.exe' if os.name == 'nt' else ''))
    candidates = [override, str(Path(getattr(sys, '_MEIPASS', base_dir())) / 'tools' / name), str(bundled), shutil.which(name)]
    if sys.platform == 'darwin':
        candidates += [f'/opt/homebrew/bin/{name}', f'/usr/local/bin/{name}']
    found = next((path for path in candidates if path and os.path.isfile(path)
                  and os.access(path, os.X_OK)), None)
    if not found:
        raise RuntimeError(f'Не найден {name}. Установите FFmpeg или задайте FADE_TOOLS_{name.upper()}.')
    return found


def run(args, timeout=None):
    p = subprocess.run([str(a) for a in args], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                       timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr.decode('utf-8', errors='replace')[-12000:] or
                           f'Процесс завершился с кодом {p.returncode}')
    if p.stderr:
        logging.warning('Tool diagnostics: %s', p.stderr.decode('utf-8', errors='replace')[-12000:])
    return p.stdout


def probe(path, count=False, cancellation=None):
    args = [tool('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-show_chapters']
    if count:
        args += ['-count_frames']
    execute = cancellation.run if cancellation else run
    info = json.loads(execute(args + ['-of', 'json', path], timeout=None))
    missing = [s for s in info['streams'] if s['codec_type'] in ('audio', 'video')
               and s.get('duration') in (None, 'N/A')]
    for s in missing:
        tag = s.get('tags', {}).get('DURATION')
        if tag:
            h, m, sec = tag.split(':')
            s['duration'] = str(int(h)*3600 + int(m)*60 + float(sec) - float(s.get('start_time', 0)))
    # Some containers have no per-stream durations. Scan packet timestamps with
    # bounded memory; never substitute the longest track's container duration.
    missing = [s for s in missing if s.get('duration') in (None, 'N/A')]
    if missing:
        ends = {}
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen([tool('ffprobe'), '-v', 'error', '-show_packets',
                '-show_entries', 'packet=stream_index,pts_time,duration_time', '-of', 'compact', str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if cancellation:
                cancellation.bind(proc)
            for line in proc.stdout:
                fields = dict(x.split('=', 1) for x in line.decode('utf-8').strip().split('|') if '=' in x)
                try:
                    idx = int(fields['stream_index'])
                    end = float(fields['pts_time']) + float(fields['duration_time'])
                    ends[idx] = max(ends.get(idx, end), end)
                except (ValueError, KeyError):
                    continue
            proc.stdout.close()
            result = proc.wait()
            if cancellation:
                cancellation.release(proc)
                cancellation.check()
            if result:
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8', errors='replace')[-4000:])
        for s in missing:
            if s['index'] in ends:
                s['duration'] = str(ends[s['index']] - float(s.get('start_time', 0)))
    return info


def duration(stream, info):
    # Never use container duration for an individually shorter audio track.
    if stream.get('duration') not in (None, 'N/A'):
        value = float(stream['duration'])
    elif stream.get('duration_ts') is not None and stream.get('time_base'):
        value = float(Fraction(stream['time_base']) * int(stream['duration_ts']))
    else:
        raise ValueError('Нет точной длительности дорожки. Сначала экспортируйте файл в MOV/MP4.')
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Некорректная длительность дорожки')
    return value


def inspect(info):
    videos = [s for s in info['streams'] if s['codec_type'] == 'video']
    if len(videos) != 1 or videos[0].get('disposition', {}).get('attached_pic'):
        raise ValueError('Требуется один видеопоток без обложки/дополнительных ракурсов')
    v = videos[0]
    if any(s['codec_type'] not in ('video', 'audio', 'data') for s in info['streams']):
        raise ValueError('Файл содержит субтитры/вложения. Их перенос в MOV не гарантирован.')
    for s in info['streams']:
        if s['codec_type'] == 'data' and s.get('codec_tag_string') != 'tmcd':
            raise ValueError('Неизвестная дополнительная дорожка данных; обработка отменена')
    if v.get('color_transfer') in ('smpte2084', 'arib-std-b67') or any(
            x.get('side_data_type') in ('DOVI configuration record', 'Mastering display metadata',
                                      'HDR Dynamic Metadata SMPTE2094-40 (HDR10+)') for x in v.get('side_data_list', [])):
        raise ValueError('HDR/Dolby Vision пока не поддерживаются: метаданные и яркость требуют отдельной проверки')
    if any(x.get('side_data_type') == 'Display Matrix' for x in v.get('side_data_list', [])):
        raise ValueError('Видео с поворотом Display Matrix пока не поддерживается. Экспортируйте с применённым поворотом.')
    if v.get('field_order') not in (None, 'unknown', 'progressive'):
        raise ValueError('Чересстрочное видео пока не поддерживается')
    pix = v.get('pix_fmt', '')
    if pix.startswith(('yuva', 'gbrap', 'rgba', 'bgra', 'argb', 'abgr')):
        raise ValueError('Видео с альфа-каналом требует отдельного режима экспорта')
    fps = float(Fraction(v.get('avg_frame_rate') or v.get('r_frame_rate') or '0'))
    if not fps > 0:
        raise ValueError('Не удалось определить FPS')
    audios = [s for s in info['streams'] if s['codec_type'] == 'audio']
    for s in audios:
        if not s.get('channel_layout'):
            raise ValueError('Неизвестная канальная структура аудио; угадывание отключено')
    return v, audios, fps


def pair_filters(cfg, prefix, fps, length, offset, video=False, frame_count=None):
    a, b = [seconds(cfg['fades'][prefix + '_' + edge], fps) for edge in ('in', 'out')]
    if a + b > length + 1e-7:
        raise ValueError(f'{prefix}: сумма Fade In/Out ({a+b:.3f} с) больше длительности ({length:.3f} с)')
    filters = []
    for edge, d in (('in', a), ('out', b)):
        if not d:
            continue
        f = cfg['fades'][prefix + '_' + edge]
        if video and f['unit'] == 'frames':
            n = int(f['value'])
            start = 0 if edge == 'in' else max(0, frame_count - 1 - n)
            filters.append(f'fade=t={edge}:s={start}:n={n}')
        else:
            # End at the final image timestamp, rather than after the final frame.
            end = max(0, length - (1 / fps if video else 0))
            start = offset if edge == 'in' else offset + max(0, end - d)
            filters.append(f'{"fade" if video else "afade"}=t={edge}:st={start:.9f}:d={d:.9f}')
    return filters


def encoding(cfg, v):
    pix = v.get('pix_fmt', '')
    ten = int(v.get('bits_per_raw_sample') or 0) > 8 or any(x in pix for x in ('10', '12', '16'))
    q = {'master': 14, 'high': 18, 'compact': 23}[cfg['quality']]
    codec = cfg['codec']
    if cfg['engine'] == 'Apple VideoToolbox' and codec in ('H.264', 'H.265'):
        bitrate = round(cfg['video_bitrate_mbps'] * 1_000_000)
        return ['-c:v', 'hevc_videotoolbox' if codec == 'H.265' else 'h264_videotoolbox',
                '-allow_sw', '0', '-realtime', '0', '-b:v', str(bitrate),
                '-profile:v', 'main10' if ten and codec == 'H.265' else ('main' if codec == 'H.265' else 'high'),
                '-pix_fmt', 'p010le' if ten else 'yuv420p'] + (['-tag:v', 'hvc1'] if codec == 'H.265' else [])
    if codec == 'H.265':
        bitrate = round(cfg['video_bitrate_mbps'] * 1_000_000)
        if cfg['engine'] == 'NVENC':
            return ['-c:v', 'hevc_nvenc', '-preset', 'p7', '-tune', 'uhq',
                    '-rc', 'vbr', '-b:v', str(bitrate), '-maxrate:v', str(bitrate * 2),
                    '-bufsize:v', str(bitrate * 8), '-multipass', 'fullres',
                    '-rc-lookahead', '16', '-lookahead_level', '0',
                    '-spatial-aq', '1', '-temporal-aq', '0', '-aq-strength', '8',
                    '-no-scenecut', '0', '-b_adapt', '1', '-nonref_p', '0',
                    '-profile:v', 'main10' if ten else 'main',
                    '-pix_fmt', 'p010le' if ten else 'yuv420p', '-tag:v', 'hvc1']
        return ['-c:v', 'libx265', '-preset', 'veryslow', '-b:v', str(bitrate),
                '-maxrate:v', str(bitrate * 2), '-bufsize:v', str(bitrate * 8),
                '-pix_fmt', 'yuv420p10le' if ten else 'yuv420p', '-tag:v', 'hvc1']
    if codec in ('H.264', 'H.265'):
        if cfg['engine'] == 'NVENC':
            enc = 'h264_nvenc' if codec == 'H.264' else 'hevc_nvenc'
            args = ['-c:v', enc, '-preset', 'p7', '-tune', 'hq', '-rc', 'vbr',
                    '-cq', str(q), '-b:v', '0', '-multipass', 'fullres',
                    '-pix_fmt', 'p010le' if ten else 'yuv420p']
            if ten:
                args += ['-profile:v', 'high10' if codec == 'H.264' else 'main10']
        else:
            args = ['-c:v', 'libx264' if codec == 'H.264' else 'libx265',
                    '-preset', 'slow', '-crf', str(q), '-pix_fmt', 'yuv420p10le' if ten else 'yuv420p']
        if codec == 'H.265':
            args += ['-tag:v', 'hvc1']
        return args
    if codec == 'ProRes':
        return ['-c:v', 'prores_ks', '-profile:v', '3' if cfg['quality'] != 'compact' else '2',
                '-pix_fmt', 'yuv422p10le', '-vendor', 'apl0']
    return ['-c:v', 'dnxhd', '-profile:v', 'dnxhr_hqx' if cfg['quality'] != 'compact' else 'dnxhr_sq',
            '-pix_fmt', 'yuv422p10le' if cfg['quality'] != 'compact' else 'yuv422p']


def encode_with_fallback(args, cfg, video, target, execute, status=None):
    """Retry a failed hardware encode once on CPU; never catch cancellation."""
    hardware = cfg['engine'] == 'Apple VideoToolbox' and cfg['codec'] in ('H.264', 'H.265')
    if status:
        status('Apple VideoToolbox' if hardware else cfg['engine'])
    try:
        return execute(args)
    except RuntimeError as exc:
        if not hardware:
            raise
        logging.warning('VideoToolbox failed; retrying on CPU: %s', exc)
        Path(target).unlink(missing_ok=True)
        cpu = dict(cfg, engine='CPU')
        old, new = encoding(cfg, video), encoding(cpu, video)
        index = next(i for i in range(len(args)) if args[i:i + len(old)] == old)
        fallback = args[:index] + new + args[index + len(old):]
        if status:
            status('CPU · VideoToolbox недоступен')
        return execute(fallback)


def command(source, target, cfg, info):
    validate(cfg)
    v, audios, fps = inspect(info)
    origin = float(info.get('format', {}).get('start_time', 0))
    args = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'warning', '-nostdin', '-n',
            '-copyts', '-start_at_zero', '-noautorotate', '-i', source,
            '-map', f'0:{v["index"]}']
    for s in audios:
        args += ['-map', f'0:{s["index"]}']
    # Timecode is remuxed from metadata by the MOV muxer; unknown data was rejected.
    args += ['-map_metadata', '0', '-map_chapters', '0']
    args += ['-map_metadata:s:v:0', f'0:s:{v["index"]}']
    filters = pair_filters(cfg, 'video', fps, duration(v, info), float(v.get('start_time', 0)) - origin,
                           True, int(v.get('nb_frames') or v.get('nb_read_frames') or 0))
    if filters:
        args += ['-vf', ','.join(filters)]
    args += encoding(cfg, v) + ['-fps_mode:v', 'passthrough']
    for key, flag in (('color_range', '-color_range'), ('color_space', '-colorspace'),
                      ('color_transfer', '-color_trc'), ('color_primaries', '-color_primaries')):
        if v.get(key) not in (None, 'unknown', 'unspecified'):
            args += [flag, v[key]]
    for i, s in enumerate(audios):
        args += [f'-map_metadata:s:a:{i}', f'0:s:{s["index"]}']
        filters = pair_filters(cfg, 'audio', fps, duration(s, info), float(s.get('start_time', 0)) - origin)
        if filters:
            args += [f'-filter:a:{i}', ','.join(filters)]
        if cfg['codec'] == 'H.265':
            args += [f'-c:a:{i}', 'aac', f'-b:a:{i}', str(160000 * int(s['channels']))]
        else:
            args += [f'-c:a:{i}', 'pcm_s24le']
        args += [f'-ar:a:{i}', str(s['sample_rate'])]
        args += [f'-metadata:s:a:{i}', 'language=' + s.get('tags', {}).get('language', 'und')]
        if s.get('tags', {}).get('title'):
            args += [f'-metadata:s:a:{i}', 'title=' + s['tags']['title']]
        dispositions = '+'.join(k for k, val in s.get('disposition', {}).items() if val) or '0'
        args += [f'-disposition:a:{i}', dispositions]
    # Network optimization is off in the reference Resolve preset.
    args += ['-movflags', '+use_metadata_tags' if cfg['codec'] == 'H.265' else '+faststart+use_metadata_tags', target]
    return [str(a) for a in args]


def verify(before, after):
    v, audios, fps = inspect(before)
    out_v, out_a, out_fps = inspect(after)
    for key in ('width', 'height', 'sample_aspect_ratio'):
        if v.get(key, '1:1') != out_v.get(key, '1:1'):
            raise ValueError(f'Проверка результата: изменилось {key}')
    if abs(fps - out_fps) > 0.001:
        raise ValueError('Проверка результата: изменился FPS')
    n1 = int(v.get('nb_frames') or v.get('nb_read_frames') or 0)
    n2 = int(out_v.get('nb_frames') or out_v.get('nb_read_frames') or 0)
    if not n1 or n1 != n2:
        raise ValueError(f'Проверка результата: число кадров {n1} → {n2}')
    if len(audios) != len(out_a):
        raise ValueError('Проверка результата: изменилось число аудиодорожек')
    for s, out in zip([v] + audios, [out_v] + out_a):
        relative_before = float(s.get('start_time', 0)) - float(v.get('start_time', 0))
        relative_after = float(out.get('start_time', 0)) - float(out_v.get('start_time', 0))
        if abs(relative_before - relative_after) > max(0.01, 1 / fps):
            raise ValueError('Проверка результата: изменилось смещение аудио/видео')
        if abs(duration(s, before) - duration(out, after)) > max(0.05, 1 / fps):
            raise ValueError('Проверка результата: изменилась длительность дорожки')
        for key in ('channels', 'channel_layout', 'sample_rate', 'color_range', 'color_space',
                    'color_transfer', 'color_primaries'):
            if s.get(key) not in (None, 'unknown', 'unspecified') and s[key] != out.get(key):
                raise ValueError(f'Проверка результата: изменилось {key}: {s[key]} → {out.get(key)}')
        for key in ('language', 'title'):
            value = s.get('tags', {}).get(key)
            if value and value != 'und' and value != out.get('tags', {}).get(key):
                raise ValueError(f'Проверка результата: потеряно {key}')


def process(source, cfg):
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise ValueError('На Droplet нужно перетащить видеофайл')
    validate(cfg)
    info = probe(source)
    v, _, _ = inspect(info)
    if not v.get('nb_frames'):
        info = probe(source, count=True)
    outdir = Path(cfg['output_dir']).expanduser() if cfg['output_dir'] else source.parent
    outdir.mkdir(parents=True, exist_ok=True)
    extension = '.mp4' if cfg['codec'] == 'H.265' else '.mov'
    fd, name = tempfile.mkstemp(prefix='.fade-', suffix=extension, dir=outdir)
    os.close(fd)
    tmp = Path(name)
    tmp.unlink()  # FFmpeg -n creates the temporary file itself.
    try:
        args = command(source, tmp, cfg, info)
        logging.info('START %s settings=%s command=%s', source, cfg, args)
        encode_with_fallback(args, cfg, v, tmp, run)
        after = probe(tmp)
        if not after['streams'][0].get('nb_frames'):
            after = probe(tmp, count=True)
        verify(info, after)
        if cfg['codec'] == 'H.265':
            out_video = next(s for s in after['streams'] if s['codec_type'] == 'video')
            if out_video['codec_name'] != 'hevc' or 'mp4' not in after['format']['format_name']:
                raise ValueError('Проверка результата: ожидался H.265 в MP4')
            logging.info('VIDEO RATE target_mbps=%s measured_mbps=%.3f (VBR target, not a hard floor)',
                         cfg['video_bitrate_mbps'], int(out_video.get('bit_rate', 0)) / 1_000_000)
        # Decode every video/audio packet; corrupt/incomplete outputs never publish.
        run([tool('ffmpeg'), '-v', 'error', '-xerror', '-nostdin', '-i', tmp,
             '-map', '0:v', '-map', '0:a?', '-f', 'null', '-'])
        for i in range(100000):
            extra = '' if i == 0 else f'_{i+1}'
            target = outdir / (source.stem + cfg['suffix'] + extra + extension)
            if target == source:
                continue
            try:
                # Windows rename is atomic and refuses an existing destination.
                if os.name == 'nt':
                    os.rename(tmp, target)
                else:
                    os.link(tmp, target)
                    tmp.unlink()
                logging.info('SUCCESS %s', target)
                return target
            except FileExistsError:
                continue
        raise RuntimeError('Не удалось подобрать свободное имя')
    finally:
        tmp.unlink(missing_ok=True)

