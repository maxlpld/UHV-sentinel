"""Headless GUI smoke test; requires Xvfb/Tk, stubs all serial hardware."""
from __future__ import annotations
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ['XDG_DATA_HOME']=tempfile.mkdtemp(prefix='uhv-sentinel-tdk-gui-')
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'tests'))
import serial
from test_tdk_profiles import FakeTDKSerial
from test_protocols import FakeSerial
import uhv_sentinel as gui

class Port:
    def __init__(self,device):self.device=device

class DispatchSerial:
    def __new__(cls,port,*a,**kw):
        if port=='COM77': return FakeTDKSerial(port,*a,**kw)
        return FakeSerial(port,*a,**kw)

serial.Serial=DispatchSerial
# GUI uses the real (stubbed) serial.tools.list_ports namespace.
gui.list_ports.comports=lambda:[Port('COM77'),Port('COM78')]
app=gui.UHVSentinelApp()
try:
    app.device_type_var.set(gui.TDK_DEVICE_TYPE)
    app.on_device_type_changed()
    app.tdk_protocol_var.set('Genesys (legacy GEN RS232)')
    app.port_var.set('COM77')
    app.name_var.set('TDK heater')
    app.add_selected_controller()
    app.device_type_var.set(gui.PFEIFFER_DEVICE_TYPE)
    app.on_device_type_changed()
    app.port_var.set('COM78')
    app.name_var.set('Chamber')
    app.add_selected_controller()
    for _ in range(50):
        app.update()
        time.sleep(.04)
    assert len(app.panels)==2,len(app.panels)
    tdk=app.panels[0]
    assert tdk.device_type==gui.TDK_DEVICE_TYPE,tdk.device_type
    assert tdk._latest_status is not None,tdk.error_var.get()
    assert float(tdk.current_var.get().replace(' A',''))==1.85,tdk.current_var.get()
    assert not tdk._armed
    assert tdk.out_on_btn.instate(['disabled'])
    assert len(gui.TDK_HISTORY.rows('COM77'))>=1
    hist=app.web_monitor.history_snapshot('COM77','1h')
    assert hist['kind']=='electrical',hist
    assert set(s['key'] for s in hist['series'])=={'Voltage','Current'}
    app.layout_columns_var.set(1)
    app.relayout_panels()
    app.reorder_combo.current(0)
    app.move_selected_panel(1)
    assert app.panels[0].port=='COM78'
    profile=gui.profiles.save_profile('Lab-Setup',app._config_data())
    assert profile.exists()
    payload=gui.profiles.load_profile(profile)
    assert payload['config']['layout']['columns']==1
    assert payload['config']['controllers'][0]['port']=='COM78'
    assert not payload['config']['alert_settings']['remote_control_enabled']
    app._load_config_data(payload['config'],replace_existing=True)
    for _ in range(50):
        app.update()
        time.sleep(.04)
    assert len(app.panels)==2,len(app.panels)
    assert app.panels[1].port=='COM77'
    assert app.panels[1]._latest_status is not None
    assert not app.panels[1]._armed
    assert app.layout_columns_var.get()==1
    app.open_profiles_window()
    app.update()
    assert any(isinstance(w,gui.tk.Toplevel) and 'Profiles' in w.title() for w in app.winfo_children())
    print('TDK + Pfeiffer mock GUI, DB, browser, profile ordering, reload, and disarmed state: PASS')
finally:
    app.on_close()
