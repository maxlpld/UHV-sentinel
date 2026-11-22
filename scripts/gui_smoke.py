"""Run with Xvfb (Linux) or on a desktop; devices are mocked, no real hardware."""
from __future__ import annotations
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault('XDG_DATA_HOME', tempfile.mkdtemp(prefix='uhv-gui-smoke-'))
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
import serial
from test_protocols import FakeSerial
serial.Serial = FakeSerial
import uhv_sentinel as gui

app = gui.UHVSentinelApp()
for device_type, port in ((gui.AGILENT_DEVICE_TYPE, 'COM44'), (gui.EPIMAX_DEVICE_TYPE, 'COM45')):
    app.device_type_var.set(device_type)
    app.on_device_type_changed()
    app.port_var.set(port)
    app.add_selected_controller()
for _ in range(35):
    app.update()
    time.sleep(.06)
assert len(app.panels) == 2
for panel in app.panels:
    assert panel._latest_status is not None, (panel.device_type, panel.error_var.get())
    assert panel._latest_status.ok, (panel.device_type, panel.error_var.get())
    print('Connected simulated device:', panel.device_type, list(panel._latest_status.gauges))
    history = app.web_monitor.history_snapshot(panel.port, '1h')
    assert set(item['key'] for item in history['series']) == set(panel.gauge_keys)
app.open_options_window()
app.update()
options_windows = [w for w in app.winfo_children() if isinstance(w, gui.tk.Toplevel) and 'Options' in w.title()]
assert options_windows
opts = options_windows[-1]
def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)
buttons = [x for x in walk(opts) if isinstance(x, gui.ttk.Button)]
update_buttons = [x for x in buttons if x.cget('text').startswith('Update')]
assert len(update_buttons) == 1, [(x.cget('text')) for x in buttons]
update_buttons[0].invoke()
app.update()
assert any('Updates' in w.title() for w in walk(app) if isinstance(w, gui.tk.Toplevel))
print('Settings -> Update dialog OK')
app.on_close()
print('GUI smoke test PASSED')
