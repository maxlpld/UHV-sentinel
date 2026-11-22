"""IMPAC IN 140/5, IN 140/5-H, IN 140/5-L and IS/IGA 140 UPP driver.

The instrument's documented protocol is ASCII, 8 data bits, EVEN parity,
1 stop bit, address + two lowercase command letters + CR. This driver never
writes instrument settings during initialisation, reconnection, or scanning.

References: LumaSense IN 140/5 Series Manual (2009), section 14, and
IS 140 / IGA 140 Manual section 9. The pyrometer needs an appropriate
RS232 or RS485 adapter; the physical switch cannot be changed in software.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

import serial

MODEL_RE = re.compile(r"^(?:IN\s*140\s*/\s*5(?:\s*[- ]\s*[HL])?|(?:IS|IGA)\s*140(?:-L)?)$", re.IGNORECASE)
T90 = {
    0: "Minimum (intrinsic)", 1: "0.01 s", 2: "0.05 s", 3: "0.25 s",
    4: "1.00 s", 5: "3.00 s", 6: "10.00 s",
}
CLEAR = {
    0: "Off", 1: "0.01 s", 2: "0.05 s", 3: "0.25 s",
    4: "1.00 s", 5: "5.00 s", 6: "25.00 s", 7: "External",
    8: "Automatic", 9: "Hold",
}
BAUDS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
OPTIONAL_SETTINGS = ("em", "ez", "lz", "as", "fh", "mb", "me", "gt", "tm",
                     "fs", "sn", "vs", "bn", "in", "tw")

BAUD_CODE = {1200: 0, 2400: 1, 4800: 2, 9600: 3, 19200: 4,
             38400: 5, 57600: 6, 115200: 8}


class Impac140:
    """Driver for a directly attached IMPAC IN 140/5 / IS 140 / IGA 140.

    ``discover`` must only be called on an isolated / single-instrument port:
    it can use the documented *replying* global query address 99. On a shared
    RS485 bus several instruments might reply simultaneously. No broadcast or
    other setting command is ever sent during discovery.
    """

    def __init__(self, port: str, baudrate: int = 19200, address: int = 0):
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.address = int(address)
        if not 0 <= self.address <= 97:
            raise ValueError("UPP device address must be between 00 and 97")
        if self.baudrate not in BAUDS:
            raise ValueError("Unsupported UPP baud rate")
        self.ser: Optional[serial.Serial] = None
        self.lock = threading.RLock()
        self.identity = ""
        self.verified = False
        self.probe_log: list[str] = []
        self.last_tx: bytes = b""
        self.last_rx: bytes = b""
        # Optional diagnostic queries that timed out are skipped until the
        # user requests another full settings refresh. Temperature reads are
        # NEVER added to this list.
        self.unavailable_settings: dict[str, str] = {}
        # Set by the owning GUI worker. Allows a fast, clean shutdown even
        # while optional settings are being polled.
        self.cancel_event: Optional[threading.Event] = None

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Pyrometer connection is shutting down")

    def _connect_port(self) -> None:
        self._check_cancelled()
        if self.ser is None or not self.ser.is_open:
            # Never retain an unusable serial handle following a USB unplug.
            # On Windows an unavailable COM port can reappear a moment later.
            self.ser = None
            connection = serial.Serial(
                port=self.port, baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE, timeout=0.08,
                write_timeout=1.0, xonxoff=False, rtscts=False, dsrdtr=False,
            )
            self.ser = connection
            # Allow USB converters to settle after the operating system opens
            # their handle. No settings are written to the instrument.
            if self.cancel_event is not None:
                if self.cancel_event.wait(0.12):
                    raise InterruptedError("Pyrometer connection is shutting down")
            else:
                time.sleep(0.12)
            self._check_cancelled()

    @staticmethod
    def _parse_model(reply: str) -> str:
        """Accept only the model designations documented by the manufacturers.

        UPP `na` replies have a fixed 16-character field, often space/NUL padded.
        """
        model = " ".join(reply.replace("\x00", " ").strip().upper().split())
        model = re.sub(r"\s*/\s*", "/", model)
        model = re.sub(r"\s*-\s*", "-", model)
        if MODEL_RE.fullmatch(model):
            return model
        raise RuntimeError(
            f"The COM port replied with model {reply!r}, which is not a supported "
            "IMPAC IN 140/5(-H/-L), IS 140(-L) or IGA 140(-L). "
            "Configuration writes are disabled; check the exact type label."
        )

    def _identify(self, timeout: float = 1.0) -> str:
        """Read-only model identification, with IN 140/5 firmware fallback.

        Most firmware responds to `na`. If this firmware cannot answer `na`,
        accept ONLY a valid UPP measuring value plus the IN 140/5's documented
        `ve` prefix 60; a measuring value alone is not enough to enable writes.
        """
        try:
            raw = self._exchange("na", timeout=timeout)
        except (TimeoutError, RuntimeError) as exc:
            if isinstance(exc, RuntimeError) and "rejected UPP command" not in str(exc):
                raise
            id_error = str(exc)
        else:
            return self._parse_model(raw)
        try:
            value = self._exchange("ms", timeout=timeout)
            if not re.fullmatch(r"\d{5}", value):
                raise RuntimeError(f"Unexpected UPP measurement: {value!r}")
            version = self._exchange("ve", timeout=timeout)
            if re.fullmatch(r"60\d{4}", version):
                return "IN 140/5 (firmware type 60)"
            raise RuntimeError(f"UPP answered, but firmware type is not IN 140/5: {version!r}")
        except (TimeoutError, RuntimeError, ValueError) as exc:
            raise TimeoutError(
                f"No confirmed IN/IS/IGA 140 identity on {self.port}, "
                f"address {self.address:02d}, {self.baudrate} baud, 8E1. "
                f"Model query: {id_error}; secondary read-only check: {exc}. "
                "Check RS232/RS485 switch, adapter type, wiring, power, "
                "the selected COM port, and the address/baud set on the instrument."
            ) from exc

    def open(self) -> str:
        """Identify a device, retrying an initial transient USB/UPP timeout.

        If the first attempt fails, close the Windows COM handle and reopen it
        before retrying. A port left open in a half-failed state is never reused.
        The retries only send read-only identification commands.
        """
        with self.lock:
            self.verified = False
            for attempt in range(2):
                self._check_cancelled()
                try:
                    self._connect_port()
                    self.identity = self._identify()
                    self._check_cancelled()
                    self.verified = True
                    return self.identity
                except InterruptedError:
                    self.close()
                    raise
                except (serial.SerialException, TimeoutError, ConnectionError, OSError):
                    self.close()
                    if attempt == 1:
                        raise
                    if self.cancel_event is not None:
                        if self.cancel_event.wait(0.25):
                            raise InterruptedError("Pyrometer connection is shutting down")
                    else:
                        time.sleep(0.25)
                except Exception:
                    self.close()
                    raise
            raise ConnectionError("IMPAC connection failed")

    def close(self) -> None:
        with self.lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                finally:
                    self.ser = None
            self.verified = False

    def _exchange(self, mnemonic: str, value: str = "", *, timeout: float = 1.2) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9]", mnemonic):
            raise ValueError("UPP command must have two lowercase mnemonic characters")
        if not re.fullmatch(r"[A-Za-z0-9?]*", value):
            raise ValueError("Invalid UPP parameter")
        with self.lock:
            self._check_cancelled()
            if self.ser is None or not self.ser.is_open:
                raise ConnectionError(f"{self.port} is not open")
            packet = f"{self.address:02d}{mnemonic}{value}\r".encode("ascii")
            # Dispose of stale bytes, never interpret a previous command's
            # answer as the new measurement.
            self.ser.reset_input_buffer()
            self.last_tx = packet
            self.last_rx = b""
            self.ser.write(packet)
            self.ser.flush()
            deadline = time.monotonic() + timeout
            rx = bytearray()
            echoed = packet.strip().decode("ascii")
            while time.monotonic() < deadline:
                self._check_cancelled()
                chunk = self.ser.read(1)
                if not chunk:
                    continue
                if chunk == b"\r" or chunk == b"\n":
                    result = rx.decode("ascii", errors="replace").strip("\x00\n\r \t")
                    self.last_rx = bytes(rx) + chunk
                    rx.clear()
                    if not result or result == echoed:
                        # Some USB/RS485 converters echo our transmitted frame.
                        continue
                    if result.lower() == "no":
                        raise RuntimeError(f"Instrument rejected UPP command {mnemonic!r}")
                    return result
                rx.extend(chunk)
                self.last_rx = bytes(rx)
                if len(rx) > 256:
                    raise RuntimeError(f"Malformed/overlong reply on {self.port}: {bytes(rx[:50])!r}")
            if rx:
                raise TimeoutError(
                    f"{self.port}: partial reply for {packet!r}: {bytes(rx[:80])!r} "
                    "(wrong baud/parity, bad cable or converter echo?)"
                )
            raise TimeoutError(
                f"{self.port}: no reply to {packet.strip()!r} (8E1 at {self.baudrate} baud). "
                "Confirm the pyrometer's RS232/RS485 interface switch matches the "
                "physical adapter, its own baud rate/address and power. "
                "Close Pyrosoft Spot/InfraWin first. Check the RS232/RS485 selector "
                "inside the IN 140/5 and use Find connection to search "
                "documented serial baud rates/addresses."
            )

    def query(self, mnemonic: str, *, timeout: float = 1.2) -> str:
        return self._exchange(mnemonic, timeout=timeout)

    def discover(self, progress: Optional[Callable[[str], None]] = None,
                 stop_event: Optional[threading.Event] = None) -> str:
        """Read-only discovery: baud rates, supplied/00 address, global-99 query.

        The global-99 read is explicitly *not* suitable for a shared RS485 bus.
        It is used only after the GUI has obtained the user's confirmation.
        If the broadcast gives a model response, read `ga` (or `pa`) to learn
        the real unit address, then confirm it with a direct model read.
        """
        log: list[str] = []
        original_baud, original_addr = self.baudrate, self.address
        rates = list(dict.fromkeys([original_baud, 19200, 9600, 38400,
                                    4800, 57600, 2400, 115200, 1200]))
        addresses = list(dict.fromkeys([original_addr, 0, 99]))
        self.close()
        self.probe_log = []
        try:
            for baud in rates:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Pyrometer search cancelled")
                self.baudrate = baud
                # Open once per baud, rather than re-opening on every address.
                try:
                    self._connect_port()
                except serial.SerialException:
                    raise  # Busy/unavailable COM port; changing baud can't fix it.
                try:
                    for address in addresses:
                        if stop_event is not None and stop_event.is_set():
                            raise InterruptedError("Pyrometer search cancelled")
                        self.address = address
                        line = f"{self.port}: {baud} baud, UPP {address:02d}, 8E1"
                        if progress:
                            progress(f"Searching: {baud} baud · address {address:02d}")
                        try:
                            # A real UPP reply normally arrives within milliseconds;
                            # 0.45 s accommodates slower PC/USB/RS485 converters.
                            name = self._identify(timeout=0.35)
                            if address == 99:
                                # Global address 99 is queryable but is NOT a
                                # proper working address (nor safe for settings).
                                actual = self._discover_real_address()
                                self.address = actual
                                confirmed = self._identify(timeout=0.7)
                                if confirmed != name:
                                    raise RuntimeError("UPP global address identifies a different model")
                            self.identity = name
                            self.verified = True
                            log.append(f"FOUND: {self.port} {baud} 8E1 address {self.address:02d} -> {name}")
                            self.probe_log = log
                            if progress:
                                progress(f"Found {name}: {baud} baud · address {self.address:02d}")
                            return name
                        except (TimeoutError, RuntimeError, ValueError) as exc:
                            log.append(f"{line}: {str(exc)[:160]}")
                            # A different named UPP model is meaningful evidence.
                            if isinstance(exc, RuntimeError) and "replied with model" in str(exc):
                                raise
                finally:
                    if not self.verified:
                        self.close()
            raise ConnectionError(
                f"No supported IMPAC IN 140/5 / IS/IGA 140 replied on {self.port} with any "
                "documented baud rate / initial or global address. Check that "
                "the pyrometer is powered, the correct COM port is selected, "
                "and the pyrometer's RS232/RS485 switch matches your cable. "
                "The IN 140/5 has an internal physical RS232/RS485 switch. "
                "A USB-RS232 adapter cannot directly communicate with RS485, "
                "and a USB-RS485 adapter cannot directly communicate with RS232."
            )
        finally:
            self.probe_log = log
            if not self.verified:
                self.close()
                self.baudrate, self.address = original_baud, original_addr

    def _discover_real_address(self) -> int:
        """Called only after one sensor answered 99na on an isolated bus."""
        # Address-setting command without a value reads the current address.
        try:
            raw = self._exchange("ga", timeout=0.65)
            if re.fullmatch(r"\d{2}", raw) and 0 <= int(raw) <= 97:
                return int(raw)
        except (TimeoutError, RuntimeError):
            pass
        try:
            raw = self._exchange("pa", timeout=0.65)
            if re.fullmatch(r"\d{11}", raw) and 0 <= int(raw[7:9]) <= 97:
                return int(raw[7:9])
        except (TimeoutError, RuntimeError):
            pass
        raise RuntimeError(
            "Pyrometer replied at global UPP address 99 but its own address "
            "could not be read. Check the instrument's address on its keypad; "
            "do not send settings using global address."
        )

    def set(self, mnemonic: str, value: str) -> None:
        if not self.verified or self.address == 99:
            raise RuntimeError("Instrument model and individual address not verified; writes disabled")
        result = self._exchange(mnemonic, value)
        if result.lower() != "ok":
            raise RuntimeError(f"Unexpected acknowledgement for {mnemonic}: {result!r}")

    def read_temperature(self) -> tuple[Optional[float], str]:
        response = self.query("ms")
        if response in ("77770", "88880"):
            return None, ("Device overtemperature" if response == "77770" else "Temperature overflow")
        if not re.fullmatch(r"-?\d{1,6}", response):
            raise ValueError(f"Unexpected temperature reply {response!r}")
        return int(response) / 10.0, ""

    def read_settings(self, commands=None, *, retry_unavailable: bool = False) -> dict:
        """Read optional settings without mistaking a non-answer for a COM failure.

        Crucially, TimeoutError inherits from OSError in Python. It must be
        caught BEFORE OSError, otherwise a silent optional command such as
        ``00in`` causes the GUI to disconnect an otherwise healthy device.

        GUI polling may pass one command at a time to keep temperature reads
        responsive; the default still allows a full snapshot for other callers.
        """
        if retry_unavailable:
            self.unavailable_settings.clear()
        keys = OPTIONAL_SETTINGS if commands is None else tuple(commands)
        result: dict[str, object] = {}
        errors: dict[str, str] = {}
        for key in keys:
            self._check_cancelled()
            if key not in OPTIONAL_SETTINGS:
                raise ValueError(f"Unknown optional settings command: {key}")
            if key in self.unavailable_settings:
                continue
            try:
                # A short, bounded wait avoids pausing live temperature display
                # for seconds when older firmware ignores a diagnostics query.
                result[key] = self.query(key, timeout=0.55)
            except InterruptedError:
                raise
            except TimeoutError:
                # A missing OPTIONAL response does not mean the pyrometer has
                # disconnected; the next 'ms' measurement determines health.
                self.unavailable_settings[key] = "No response"
                errors[key] = "No response (optional command skipped)"
            except RuntimeError as exc:
                if "rejected UPP command" in str(exc):
                    self.unavailable_settings[key] = "Rejected by instrument"
                    errors[key] = "Unsupported/rejected optional command"
                else:
                    errors[key] = str(exc)
            except (serial.SerialException, ConnectionError, OSError):
                # A real USB/serial transport error still triggers reconnect.
                raise
            except (ValueError, UnicodeError) as exc:
                errors[key] = str(exc)
        result["_errors"] = errors
        result["_unavailable"] = tuple(self.unavailable_settings)
        return result

    def set_emissivity(self, percent: float) -> None:
        max_emissivity = 120.0 if self.identity.startswith("IN 140/5") else 100.0
        if not (10.0 <= percent <= max_emissivity):
            raise ValueError(f"Emissivity must be between 10 and {max_emissivity:g} %")
        # IN 140/5 parameter section documents 120%, but its UPP command
        # table lists 1000 permille as maximum. The instrument must ACK the
        # command; firmware that enforces 100% will refuse larger values.
        self.set("em", f"{round(percent * 10):04d}")

    def set_choice(self, command: str, code: int) -> None:
        valid = {"ez": set(T90), "lz": set(CLEAR), "as": {0, 1},
                 "fh": {0, 1}, "la": {0, 1}}
        if command not in valid or code not in valid[command]:
            raise ValueError("Unsupported choice or invalid command")
        self.set(command, str(code))

    def clear_peak(self) -> None:
        self.set("lx", "")

    def set_wait_time(self, value: int) -> None:
        if not 0 <= value <= 99:
            raise ValueError("Wait time must be 00..99")
        self.set("tw", f"{value:02d}")

    @staticmethod
    def parse_range(raw: str) -> tuple[int, int]:
        if not re.fullmatch(r"[0-9A-Fa-f]{8}", raw):
            raise ValueError(f"Invalid range reply {raw!r}")
        return int(raw[:4], 16), int(raw[4:], 16)

    def set_subrange(self, lower: int, upper: int, full: tuple[int, int]) -> None:
        if not (full[0] <= lower < upper <= full[1]):
            raise ValueError(f"Subrange must lie within instrument range {full[0]}..{full[1]} °C")
        if self.identity.startswith("IN 140/5") and upper - lower < 51:
            raise ValueError("The IN 140/5 requires a subrange span of at least 51 °C")
        self.set("m1", f"{lower:04X}{upper:04X}")
