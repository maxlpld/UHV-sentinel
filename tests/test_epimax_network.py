"""Mocked Modbus TCP / RTU-over-TCP tests (no lab hardware)."""
import math
import struct
import unittest
from unittest.mock import patch
from epimax_network import EpimaxTCPDevice, make_endpoint, parse_endpoint
from vacuum_extra_devices import modbus_crc16

class FakeSocket:
    def __init__(self):
        self.sent=[]
        self.buf=bytearray()
    def settimeout(self,v): pass
    def close(self): pass
    def sendall(self,packet):
        self.sent.append(packet)
        payload=struct.pack('<f',2.5e-9)
        if packet[2:4]==b'\0\0' and packet[7]==0x17:
            tx,_,_,unit=struct.unpack('>HHHB',packet[:7])
            pdu=b'\x17\x04'+payload
            self.buf.extend(struct.pack('>HHHB',tx,0,len(pdu)+1,unit)+pdu)
        else:
            reply=b'\x01\x17\x04'+payload
            self.buf.extend(reply+struct.pack('<H',modbus_crc16(reply)))
    def recv(self,n):
        data=self.buf[:n]
        del self.buf[:n]
        return bytes(data)

class NetworkTests(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(make_endpoint('10.1.1.20',502,'modbus_tcp'),'tcp://10.1.1.20:502')
        self.assertEqual(parse_endpoint('rtu+tcp://10.1.1.20:4001'),('10.1.1.20',4001,'rtu_tcp_bridge'))
        with self.assertRaises(ValueError):make_endpoint('evil/path',502,'modbus_tcp')
    def test_modbus_tcp(self):
        f=FakeSocket()
        with patch('epimax_network.socket.create_connection',return_value=f):
            d=EpimaxTCPDevice('tcp://10.1.1.20:502')
            d.open()
            p=d.read_float(0x9a)
            d.close()
        self.assertTrue(math.isclose(p,2.5e-9,rel_tol=1e-6))
        self.assertEqual(f.sent[0][14:16],b'\0\0')
    def test_rtu_tcp_gateway(self):
        f=FakeSocket()
        with patch('epimax_network.socket.create_connection',return_value=f):
            d=EpimaxTCPDevice('rtu+tcp://10.1.1.20:4001')
            d.open()
            p=d.read_float(0x9a)
            d.close()
        self.assertTrue(math.isclose(p,2.5e-9,rel_tol=1e-6))
        self.assertEqual(modbus_crc16(f.sent[0][:-2]),int.from_bytes(f.sent[0][-2:],'little'))
    def test_error(self):
        with patch('epimax_network.socket.create_connection',side_effect=OSError('disconnected')):
            with self.assertRaisesRegex(ConnectionError,'RJ45 serial'):
                EpimaxTCPDevice('tcp://10.1.1.20:502').open()
