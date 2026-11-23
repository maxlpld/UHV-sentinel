"""Run: python -m unittest discover -s tests -v (pyserial installed)."""
import math
import struct
import unittest
from unittest.mock import patch

from vacuum_extra_devices import (agilent_crc, agilent_read_frame,
    parse_agilent_response, epimax_fc23_read_request, parse_epimax_fc23_response,
    modbus_crc16, Agilent4UHVDevice, EpimaxPVCDevice)


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.is_open = True
        self.sent = []
        self.buf = bytearray()

    def reset_input_buffer(self):
        self.buf.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def write(self, req):
        self.sent.append(req)
        if req.startswith(b'\x02\x80'):
            window = int(req[2:5])
            if window in (812, 822):
                value = '  1.5E-08' if window == 812 else '  2.9E-09'
            elif window == 600:
                value = '000001'
            elif window in (811, 821):
                value = '  2.9E-05'
            elif window in (810, 820):
                value = '003000'
            else:
                value = '000001'
            body = b'\x80' + req[2:5] + b'0' + value.encode('ascii') + b'\x03'
            self.buf.extend(b'\x02' + body + agilent_crc(body))
        elif len(req) >= 13 and req[1] == 0x17:
            address = int.from_bytes(req[2:4], 'big')
            payload = struct.pack('<f', 7.25e-10) if address == 0x9A else b'\0'*4
            raw = bytes((req[0], 0x17, len(payload))) + payload
            self.buf.extend(raw + struct.pack('<H', modbus_crc16(raw)))
        return len(req)

    def read(self, length=1):
        got = self.buf[:length]
        del self.buf[:length]
        return bytes(got)


class ProtocolTests(unittest.TestCase):
    def test_agilent_request_matches_manual(self):
        self.assertEqual(agilent_read_frame(812), bytes.fromhex('02 80 38 31 32 30 03 38 38'))

    def test_agilent_read_response_and_crc_rejection(self):
        body = b'\x80' + b'8120' + b'   1.5E-08' + b'\x03'
        frame = b'\x02' + body + agilent_crc(body)
        self.assertEqual(parse_agilent_response(frame, 812), '1.5E-08')
        with self.assertRaises(ValueError):
            parse_agilent_response(frame[:-2] + b'00', 812)

    def test_agilent_two_channels_and_diagnostics(self):
        with patch('vacuum_extra_devices.serial.Serial', FakeSerial):
            dev = Agilent4UHVDevice('COM10')
            dev.open()
            st = dev.read_status()
            for _ in range(9):
                st = dev.read_status()  # staggered diagnostics
            dev.close()
        self.assertTrue(st.ok)
        self.assertEqual(list(st.gauges), ['Channel 1', 'Channel 2'])
        self.assertAlmostEqual(st.gauges['Channel 1'].pressure_mbar, 1.5e-8)
        self.assertAlmostEqual(st.gauges['Channel 2'].pressure_mbar, 2.9e-9)
        self.assertIn('Channel 2 Current (A)', st.diagnostics)
        self.assertIn('Fan temperature (°C)', st.diagnostics)
        # Every request uses the read command 0x30; no writes allowed.
        self.assertTrue(all(cmd[5:6] == b'0' for cmd in dev.ser.sent) if dev.ser else True)


    def test_agilent_optional_timeout_does_not_block_pressure(self):
        dev = Agilent4UHVDevice('COM10')
        windows = []
        def query(window):
            windows.append(window)
            if window == 600: return '1'
            if window in (812, 822): return '1.5E-08'
            raise TimeoutError('unsupported diagnostic window')
        with patch.object(dev, 'query_window', side_effect=query):
            first = dev.read_status()
            second = dev.read_status()
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(windows[:3], [600, 812, 822])
        self.assertEqual(windows[4:6], [812, 822])
        self.assertEqual(sum(w in (812, 822) for w in windows), 4)

    def test_agilent_reopen_after_three_failed_sweeps(self):
        with patch('vacuum_extra_devices.serial.Serial', FakeSerial):
            dev = Agilent4UHVDevice('COM10')
            dev.open()
            with patch.object(dev, 'query_window', side_effect=TimeoutError('offline')):
                for _ in range(3):
                    status = dev.read_status()
            self.assertFalse(status.ok)
            self.assertEqual(status.diagnostics['Consecutive failed pressure polls'], 3)
            self.assertEqual(status.diagnostics['Serial reconnects'], 1)
            self.assertTrue(dev.ser.is_open)
            dev.close()

    def test_agilent_eeprom_response_address(self):
        body = b'\x81' + b'8120' + b'1.5E-08' + b'\x03'
        self.assertEqual(parse_agilent_response(b'\x02' + body + agilent_crc(body), 812), '1.5E-08')

    def test_epimax_request_matches_reference_driver(self):
        req = epimax_fc23_read_request(1, 0x9A)
        self.assertEqual(req[:11], bytes.fromhex('01 17 00 9a 00 02 00 9a 00 00 00'))
        self.assertEqual(struct.unpack('<H', req[-2:])[0], modbus_crc16(req[:-2]))

    def test_epimax_pressure_and_crc_rejection(self):
        with patch('vacuum_extra_devices.serial.Serial', FakeSerial):
            dev = EpimaxPVCDevice('COM11', address=1)
            dev.open()
            st = dev.read_status()
            dev.close()
        self.assertTrue(st.ok)
        self.assertTrue(math.isclose(st.gauges['Ion gauge 1'].pressure_mbar, 7.25e-10, rel_tol=1e-6))
        raw = bytes((1, 0x17, 4)) + struct.pack('<f', 5e-9)
        response = raw + struct.pack('<H', modbus_crc16(raw))
        self.assertEqual(parse_epimax_fc23_response(response, 1), struct.pack('<f', 5e-9))
        with self.assertRaises(ValueError):
            parse_epimax_fc23_response(response[:-2] + b'XX', 1)

    def test_pressure_unit_conversion(self):
        from vacuum_extra_devices import ReadOnlyGauge
        self.assertAlmostEqual(ReadOnlyGauge('x',1,0, 1e-8, 'Pa').pressure_mbar, 1e-10)
        self.assertIsNone(ReadOnlyGauge('x',1,5, 1e-8, 'mBar').pressure_mbar)


if __name__ == '__main__':
    unittest.main()
