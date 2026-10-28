"""Windowless entry point. Each launch reads one immutable settings snapshot."""
import logging
import sys
from logging.handlers import RotatingFileHandler

from config import app_dir, load
from media import process
from notify import notify


def setup_log(name):
    root = app_dir() / 'logs'
    root.mkdir(parents=True, exist_ok=True)
    # Per process files avoid concurrent-rotation races when several drops run.
    import os
    handler = RotatingFileHandler(root / f'{name}-{os.getpid()}.log', maxBytes=2_000_000,
                                  backupCount=1, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(message)s', force=True)
    return root


def main(paths=None):
    paths = sys.argv[1:] if paths is None else paths
    try:
        logs = setup_log('droplet')
        cfg = load()
    except Exception as exc:
        notify('Fade Droplet: ошибка настроек', str(exc), True)
        return 1
    if not paths:
        notify('Fade Droplet', 'Передайте видеофайлы приложению Fade Droplet. Параметры задаются в Fade Settings.')
        return 2
    errors, outputs = [], []
    for path in paths:
        try:
            outputs.append(str(process(path, cfg)))
        except Exception as exc:
            logging.exception('FAILED %s', path)
            errors.append(f'{path}: {exc}')
    logging.info('BATCH completed=%d failed=%d', len(outputs), len(errors))
    if errors:
        notify('Fade Droplet: ошибка', f'Готово: {len(outputs)}, ошибок: {len(errors)}. '
               f'{errors[0]}\nЛоги: {logs}', True)
    elif cfg['notifications']:
        notify('Fade Droplet: готово', f'Обработано файлов: {len(outputs)}. {outputs[-1]}')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())

