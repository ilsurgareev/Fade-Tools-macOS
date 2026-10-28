"""Record startup failures, including Qt DLL import errors, without a traceback dialog."""
import sys


if __name__ == '__main__':
    try:
        from settings_app import main
        code = main()
    except Exception:
        import traceback
        from config import app_dir
        from notify import notify
        root = app_dir() / 'logs'
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'settings-startup.log'
        path.write_text(traceback.format_exc(), encoding='utf-8')
        notify('Fade Settings: ошибка запуска', f'Подробности: {path}', True)
        code = 1
    sys.exit(code)
