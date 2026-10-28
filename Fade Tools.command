#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo 'Сначала установите зависимости по README-macOS.md'
    exit 1
fi
exec .venv/bin/python settings_entry.py "$@"
