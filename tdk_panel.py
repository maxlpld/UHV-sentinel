"""Tk graphical panel for TDK-Lambda measured V/I/P and opt-in output controls."""
from __future__ import annotations
import math
import queue
import threading
import time
from tkinter import ttk, messagebox, simpledialog, filedialog
import tkinter as tk
from typing import Any, Dict, Optional
from pathlib import Path

from tdk_lambda import TDKLambdaDevice, TDKReading, GENESYS, validate_setpoint
from tdk_logging import TDKLogger

TDK_DEVICE_TYPE = "TDK-Lambda power supply"
TDK_HISTORY = TDKLogger()
RANGES = {"1 h": 3600, "24 h": 86400, "48 h": 172800, "All": None}


class TDKPanel(ttk.LabelFrame):
    device_type = TDK_DEVICE_TYPE
    def __init__(self, parent, app, name: str, port: str, baudrate: int = 9600,
                 saved_state: Optional[Dict[str, Any]] = None):
        super().__init__(parent, text=name, style="Card.TLabelframe")
        state = saved_state or {}
        self.app, self.port = app, port
        self.name_var = tk.StringVar(value=name)
        self.device = TDKLambdaDevice(port, baudrate,
                 protocol=state.get("protocol", GENESYS), address=int(state.get("address", 6)))
        self.connection_var = tk.StringVar(value="Connecting…")
        self.error_var = tk.StringVar(value="")
        self.alert_summary_var = tk.StringVar(value="Alerts: none")
        self.log_status_var = tk.StringVar(value="Not yet recorded")
        self.voltage_var = tk.StringVar(value="— V")
        self.current_var = tk.StringVar(value="— A")
        self.power_var = tk.StringVar(value="— W")
        self.output_var = tk.StringVar(value="Output: ?")
        self.setpoint_info_var = tk.StringVar(value="Setpoints: —")
        self.rated_voltage_var = tk.StringVar(value=str(state.get("voltage_limit_v", "")))
        self.rated_current_var = tk.StringVar(value=str(state.get("current_limit_a", "")))
        self.voltage_set_var = tk.StringVar(value="")
        self.current_set_var = tk.StringVar(value="")
        self.interval_var = tk.StringVar(value=str(state.get("sample_interval_s", 2)))
        self._sample_interval_s = 2.0
        self.interval_var.trace_add("write", self._on_interval_changed)
        self._on_interval_changed()
        self.graph_range_var = tk.StringVar(value=state.get("graph_range", "1 h"))
        if self.graph_range_var.get() not in RANGES:
            self.graph_range_var.set("1 h")
        self.stop_event = threading.Event()
        self.command_queue: "queue.Queue[tuple[str, float | bool, float]]" = queue.Queue()
        self.thread: Optional[threading.Thread] = None
        self._latest_status: Optional[TDKReading] = None
        self._armed = False
        self._graph_redraw_after: Optional[str] = None
        self._last_draw = 0.0
        self._last_error_at = 0.0
        self.last_logged_at = 0.0
        self.graph_canvas: Optional[tk.Canvas] = None
        self._build()
        self.thread = threading.Thread(target=self._worker, name=f"TDK-{port}", daemon=True)
        self.thread.start()

    @property
    def display_name(self):
        return self.name_var.get().strip() or self.port

    def state_for_config(self):
        return {"protocol": self.device.protocol, "address": self.device.address,
                "graph_range": self.graph_range_var.get(),
                "sample_interval_s": self.interval_var.get(),
                "voltage_limit_v": self.rated_voltage_var.get().strip(),
                "current_limit_a": self.rated_current_var.get().strip()}

    @property
    def configured_baudrate(self):
        return self.device.baudrate

    def _build(self):
        self.columnconfigure(0, weight=1)
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text=f"{self.device.protocol} · {self.port}").grid(row=0,column=0,sticky="w")
        ttk.Label(header, textvariable=self.connection_var).grid(row=0,column=1,sticky="e")
        ttk.Button(header, text="Remove panel", command=self.remove).grid(row=0,column=2,padx=(8,0))

        meters = ttk.LabelFrame(self, text="Measured output (not programmed setpoints)")
        meters.grid(row=1,column=0,sticky="ew",padx=8,pady=4)
        for col,(label,var) in enumerate((("Voltage",self.voltage_var),("Current",self.current_var),("Power",self.power_var))):
            meters.columnconfigure(col, weight=1)
            ttk.Label(meters, text=label).grid(row=0,column=col,padx=8,pady=(4,0))
            ttk.Label(meters, textvariable=var, font=("Arial", 15, "bold")).grid(row=1,column=col,padx=8,pady=(0,4))
        ttk.Label(meters, textvariable=self.output_var).grid(row=2,column=0,sticky="w",padx=8)
        ttk.Label(meters, textvariable=self.setpoint_info_var).grid(row=2,column=1,columnspan=2,sticky="e",padx=8)

        plot = ttk.LabelFrame(self, text="Actual voltage (red, left axis) / current (blue, right axis)")
        plot.grid(row=2,column=0,sticky="ew",padx=8,pady=4)
        plot.columnconfigure(3,weight=1)
        ttk.Label(plot, text="Range").grid(row=0,column=0,padx=5)
        combo = ttk.Combobox(plot, textvariable=self.graph_range_var, values=list(RANGES), width=7,state="readonly")
        combo.grid(row=0,column=1,padx=5)
        combo.bind("<<ComboboxSelected>>",lambda _e: (self._draw_graph(force=True),self.app.request_save_config()))
        ttk.Label(plot, text="Sample [s]").grid(row=0,column=2,padx=5)
        entry = ttk.Entry(plot, textvariable=self.interval_var, width=5)
        entry.grid(row=0,column=3,sticky="w",padx=5)
        entry.bind("<FocusOut>",lambda _e:self.app.request_save_config())
        ttk.Button(plot,text="CSV",command=self.export_csv).grid(row=0,column=4,padx=5)
        self.graph_canvas = tk.Canvas(plot, height=235, width=460, bg="white",highlightthickness=1)
        self.graph_canvas.grid(row=1,column=0,columnspan=5,sticky="ew",padx=6,pady=5)
        self.graph_canvas.bind("<Configure>",lambda _e:self._draw_graph(force=True))
        ttk.Label(plot,textvariable=self.log_status_var).grid(row=2,column=0,columnspan=5,sticky="w",padx=6,pady=4)

        controls = ttk.LabelFrame(self,text="Optional remote control — disarmed by default")
        controls.grid(row=3,column=0,sticky="ew",padx=8,pady=5)
        for c in range(4):
            controls.columnconfigure(c,weight=1)
        ttk.Label(controls,text="Nameplate voltage limit [V]").grid(row=0,column=0,columnspan=2,sticky="w",padx=5,pady=3)
        ttk.Entry(controls,textvariable=self.rated_voltage_var,width=8).grid(row=0,column=2,sticky="w")
        ttk.Label(controls,text="Nameplate current limit [A]").grid(row=1,column=0,columnspan=2,sticky="w",padx=5,pady=3)
        ttk.Entry(controls,textvariable=self.rated_current_var,width=8).grid(row=1,column=2,sticky="w")
        self.arm_button = ttk.Button(controls,text="Unlock controls (session only)",command=self._arm)
        self.arm_button.grid(row=2,column=0,columnspan=4,sticky="ew",padx=5,pady=5)
        ttk.Label(controls,text="Voltage setpoint [V]").grid(row=3,column=0,sticky="w",padx=5)
        ttk.Entry(controls,textvariable=self.voltage_set_var,width=9).grid(row=3,column=1,sticky="w")
        self.set_v_btn=ttk.Button(controls,text="Apply voltage",command=lambda:self._submit("voltage"))
        self.set_v_btn.grid(row=3,column=2,columnspan=2,sticky="ew",padx=5,pady=3)
        ttk.Label(controls,text="Current limit [A]").grid(row=4,column=0,sticky="w",padx=5)
        ttk.Entry(controls,textvariable=self.current_set_var,width=9).grid(row=4,column=1,sticky="w")
        self.set_i_btn=ttk.Button(controls,text="Apply current limit",command=lambda:self._submit("current"))
        self.set_i_btn.grid(row=4,column=2,columnspan=2,sticky="ew",padx=5,pady=3)
        self.out_on_btn=ttk.Button(controls,text="Output ON",command=lambda:self._submit("on"))
        self.out_on_btn.grid(row=5,column=0,columnspan=2,sticky="ew",padx=5,pady=4)
        self.out_off_btn=ttk.Button(controls,text="Output OFF",command=lambda:self._submit("off"))
        self.out_off_btn.grid(row=5,column=2,columnspan=2,sticky="ew",padx=5,pady=4)
        ttk.Label(controls,text="Never substitutes for hardware interlocks, grounding or supervision.",wraplength=450).grid(row=6,column=0,columnspan=4,sticky="w",padx=5,pady=3)
        self.update_button_states()
        ttk.Label(self,textvariable=self.error_var,wraplength=470,foreground="#d20537").grid(row=4,column=0,sticky="ew",padx=8,pady=5)

    def _on_interval_changed(self, *_args):
        # Tk variables must only be accessed from the GUI thread.
        try:
            value = float(self.interval_var.get())
            if math.isfinite(value) and 0.5 <= value <= 60.0:
                self._sample_interval_s = value
        except (ValueError, TypeError, tk.TclError):
            pass

    def _limits(self):
        v = float(self.rated_voltage_var.get())
        i = float(self.rated_current_var.get())
        validate_setpoint(0,v,"V")
        validate_setpoint(0,i,"A")
        return v,i

    def _arm(self):
        if self._armed:
            self._armed=False
            self.update_button_states()
            return
        if self._latest_status is None or time.time()-self._latest_status.timestamp > 10:
            messagebox.showerror("TDK not ready","An up-to-date hardware reading is required.",parent=self)
            return
        try:
            v,i=self._limits()
        except (ValueError,TypeError) as exc:
            messagebox.showerror("Invalid nameplate limits",str(exc),parent=self)
            return
        if simpledialog.askstring("Enable manual TDK controls",
            f"Verify supply identity: {self.device.identity}\n"
            f"Verify physical ratings and interlocks: {v:g} V / {i:g} A.\n"
            "Remote writes may immediately alter a live outgassing load.\n"
            "Type ARM to unlock for this session:",parent=self) != "ARM":
            return
        self._armed=True
        self.update_button_states()
        self.app.request_save_config()

    def _submit(self, action):
        if not self._armed or self._latest_status is None or time.time()-self._latest_status.timestamp>10:
            messagebox.showerror("Not armed","Connect and unlock controls in this session.",parent=self)
            return
        try:
            vmax,imax=self._limits()
            if action=="voltage":
                value=validate_setpoint(float(self.voltage_set_var.get()),vmax,"Voltage (V)")
                question=f"Apply voltage setpoint {value:g} V to {self.display_name}?"
            elif action=="current":
                value=validate_setpoint(float(self.current_set_var.get()),imax,"Current (A)")
                question=f"Apply current limit {value:g} A to {self.display_name}?"
            elif action=="on":
                value=True
                if self._latest_status.voltage_set_v is None or self._latest_status.current_set_a is None:
                    raise ValueError("Current setpoints not verified")
                if self._latest_status.voltage_set_v>vmax or self._latest_status.current_set_a>imax:
                    raise ValueError("Supply setpoints exceed configured limits")
                question=(f"ENERGIZE output on {self.display_name}?\n"
                          f"Current settings: {self._latest_status.voltage_set_v:g} V / "
                          f"{self._latest_status.current_set_a:g} A.\nConfirm safe load, grounding, and interlocks.")
            else:
                value=False
                question=f"Turn OFF output on {self.display_name}?"
        except (ValueError,TypeError) as exc:
            messagebox.showerror("TDK control rejected",str(exc),parent=self)
            return
        if not messagebox.askyesno("Confirm live TDK command",question,parent=self,icon="warning"):
            return
        self.command_queue.put((action,value,vmax if action=="voltage" else imax))
        self.connection_var.set(f"Sending {action}…")

    def _worker(self):
        try:
            self.device.open()
            self.app.events.put(("tdk_ident",self,self.device.identity))
        except Exception as exc:
            self.app.events.put(("error",self,str(exc)))
            return
        try:
            while not self.stop_event.is_set():
                start=time.monotonic()
                try:
                    while not self.command_queue.empty() and not self.stop_event.is_set():
                        action,value,maximum=self.command_queue.get_nowait()
                        if self.stop_event.is_set():
                            break
                        if action=="voltage": self.device.set_voltage(float(value),maximum)
                        elif action=="current": self.device.set_current(float(value),maximum)
                        else: self.device.set_output(bool(value))
                        self.app.events.put(("tdk_cmd",self,f"Command {action} accepted; verifying readback"))
                    reading=self.device.read_status()
                    self.app.events.put(("status",self,reading))
                except Exception as exc:
                    self.app.events.put(("error",self,str(exc)))
                    self.stop_event.wait(1.0)
                self.stop_event.wait(max(0.1,self._sample_interval_s-(time.monotonic()-start)))
        finally:
            self.device.close()

    def update_status(self, reading: TDKReading):
        self._latest_status=reading
        self.connection_var.set("Connected")
        self.error_var.set("")
        self.voltage_var.set(f"{reading.voltage_v:.4g} V")
        self.current_var.set(f"{reading.current_a:.5g} A")
        self.power_var.set(f"{reading.power_w:.5g} W")
        self.output_var.set("Output: "+reading.output_state+" / "+reading.mode)
        if reading.voltage_set_v is not None and reading.current_set_a is not None:
            self.setpoint_info_var.set(f"Set: {reading.voltage_set_v:g} V / {reading.current_set_a:g} A")
        try:
            TDK_HISTORY.append(self.port,self.display_name,reading)
            self.log_status_var.set("Recorded: "+time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(reading.timestamp)))
        except Exception as exc:
            self.log_status_var.set("Logging failed: "+str(exc))
        self.app.web_monitor.update_tdk(self,reading)
        self._draw_graph()

    def _draw_graph(self,force=False):
        if self.graph_canvas is None or not self.graph_canvas.winfo_exists():return
        if not force and time.monotonic()-self._last_draw<4:return
        self._last_draw=time.monotonic()
        canvas=self.graph_canvas
        canvas.delete("all")
        w,h=max(400,canvas.winfo_width()),max(210,canvas.winfo_height())
        left,right,top,bottom=48,w-54,23,h-32
        canvas.create_rectangle(left,top,right,bottom,outline="#c9c9c9")
        try: rows=TDK_HISTORY.rows(self.port,RANGES.get(self.graph_range_var.get(),3600))
        except Exception: rows=[]
        if self._latest_status is not None and (not rows or rows[-1][0]<self._latest_status.timestamp-0.01):
            r=self._latest_status
            rows.append((r.timestamp,r.voltage_v,r.current_a,r.power_w,r.voltage_set_v,r.current_set_a,r.output_state,r.mode,r.identity))
        if not rows:
            canvas.create_text(w/2,h/2,text="Awaiting electrical measurements",fill="#777")
            return
        stride=max(1,len(rows)//850)
        if stride>1: rows=rows[::stride]+([rows[-1]] if rows[-1] not in rows[::stride] else [])
        t0,t1=rows[0][0],rows[-1][0]
        if t1-t0<1:t1=t0+1
        vmax=max(0.1,max(float(r[1]) for r in rows)*1.1)
        imax=max(0.01,max(float(r[2]) for r in rows)*1.1)
        def points(k,maximum):
            coords=[]
            for row in rows:
                if not math.isfinite(float(row[k])):continue
                coords.extend((left+(row[0]-t0)/(t1-t0)*(right-left),bottom-max(0,float(row[k]))/maximum*(bottom-top)))
            return coords
        for frac in (0,.25,.5,.75,1):
            y=bottom-frac*(bottom-top)
            canvas.create_line(left,y,right,y,fill="#e5e5e5")
            canvas.create_text(left-4,y,text=f"{vmax*frac:.2g}",anchor="e",fill="#c92747",font=("Arial",8))
            canvas.create_text(right+4,y,text=f"{imax*frac:.2g}",anchor="w",fill="#196ab0",font=("Arial",8))
        for k,m,c in ((1,vmax,"#d20537"),(2,imax,"#1660a0")):
            pts=points(k,m)
            if len(pts)>=4:canvas.create_line(*pts,fill=c,width=2,smooth=False)
            elif len(pts)==2:canvas.create_oval(pts[0]-2,pts[1]-2,pts[0]+2,pts[1]+2,fill=c,outline=c)
        canvas.create_text(left,10,anchor="w",text="Voltage [V]",fill="#d20537",font=("Arial",9,"bold"))
        canvas.create_text(right,10,anchor="e",text="Current [A]",fill="#1660a0",font=("Arial",9,"bold"))
        canvas.create_text(left,bottom+14,anchor="w",text=time.strftime("%m-%d %H:%M",time.localtime(t0)),font=("Arial",8))
        canvas.create_text(right,bottom+14,anchor="e",text=time.strftime("%m-%d %H:%M",time.localtime(rows[-1][0])),font=("Arial",8))

    def export_csv(self):
        path=filedialog.asksaveasfilename(parent=self,title="Export TDK measurement history",
            initialfile=f"tdk-{self.display_name.replace(' ','-')}.csv",defaultextension=".csv",
            filetypes=[("CSV","*.csv")])
        if path:
            try:
                count=TDK_HISTORY.export_csv(self.port,Path(path))
                messagebox.showinfo("Export complete",f"Exported {count} samples to\n{path}",parent=self)
            except Exception as exc:
                messagebox.showerror("Export failed",str(exc),parent=self)

    def set_error(self,text):
        self.connection_var.set("Communication error")
        self.error_var.set(str(text))
        self.app.web_monitor.update_error(self,text)
        self._armed=False
        self.update_button_states()

    def update_button_states(self):
        enabled=self._armed
        self.arm_button.configure(text=("Lock controls" if enabled else "Unlock controls (session only)"))
        for btn in (self.set_v_btn,self.set_i_btn,self.out_on_btn,self.out_off_btn):
            btn.configure(state=("normal" if enabled else "disabled"))

    def refresh_remote_control_visibility(self):
        return

    def _force_off_button_red(self):
        return

    def remove(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():self.thread.join(timeout=2.0)
        self.device.close()
        self.app.web_monitor.remove_panel(self)
        self.app.remove_panel(self)
