"""Exercise macOS FileOpen delivery in a fresh Qt application process."""
import os
import subprocess
import sys


def test_fileopen_events_are_batched_and_processed():
    code = '''
import time
from PySide6.QtGui import QFileOpenEvent
import droplet_entry
received=[]
droplet_entry.process_batch=lambda paths: received.append(paths) or 0
app=droplet_entry.Application([])
window=droplet_entry.Droplet()
app.opened.connect(window.add)
app.sendEvent(app,QFileOpenEvent('/tmp/first video.mp4'))
app.sendEvent(app,QFileOpenEvent('/tmp/second.mp4'))
deadline=time.monotonic()+3
while (not received or window.worker is not None) and time.monotonic()<deadline:
    app.processEvents();time.sleep(.01)
assert received==[['/tmp/first video.mp4','/tmp/second.mp4']], received
assert window.worker is None and not window.pending
assert window.label.text()=='Готово.'
window.close()
'''
    result = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
