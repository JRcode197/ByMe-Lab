"""Tests offline: no real Vimar gateway required."""
import importlib.util
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import zipfile
import io
import json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('byme_lab', ROOT / 'byme_lab.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ONE = b'KNX[2]: B8 11 AA 0C 33 E2 00 80 00 A1'
TWO = b'KNX[4]: B4 11 2A 0C 8A E5 00 80 16 9D 80 51 C9'
THREE = b'KNX[0]: B4 11 2A 0C 89 E1 00 80 94'

class TestByMeLab(unittest.TestCase):
    def test_cr_lf_and_unterminated(self):
        items=[]
        framer=m.StreamFramer(items.append)
        framer.feed('KNX[0]: AA\r')
        framer.feed('\nKNX[1]: BB\rKNX[2]: CC\n')
        framer.feed('KNX[3]: DD')
        self.assertEqual(items,['KNX[0]: AA','KNX[1]: BB','KNX[2]: CC'])
        framer.flush()
        self.assertEqual(items[-1], 'KNX[3]: DD')

    def test_decoder_reference(self):
        e=m.decode_bus_line(ONE.decode())
        self.assertEqual(e['kind'],'bus')
        self.assertEqual(e['src'],'1.1.170')
        self.assertEqual(e['dst'],'1/4/51')
        self.assertEqual(e['value'],'0')
        self.assertTrue(e['checksum_ok'])
        self.assertTrue(e['length_ok'])
        self.assertEqual(m.decode_bus_line('CC')['kind'],'info')
        self.assertEqual(m.decode_bus_line('KNX[5]: CC')['kind'],'ack')

    def test_socket_capture_exports_every_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            m.BASE_DIR = Path(tmp)
            m.SESSIONS = Path(tmp)/'sessioni'
            m.SETTINGS_FILE = Path(tmp)/'impostazioni.json'
            c=m.Capture()
            srv=socket.socket()
            srv.bind(('127.0.0.1',0))
            srv.listen(1)
            port=srv.getsockname()[1]
            raw_parts=[]
            def fake_gateway():
                conn,_=srv.accept()
                with conn:
                    initial=conn.recv(4096)
                    assert initial == m.ENTER_TCP, initial
                    conn.sendall(b'#ok\r')
                    cmd=conn.recv(4096)
                    assert cmd==m.MONITOR_CMD,cmd
                    # Important: the original GUI did not handle CR-only lines.
                    chunk=ONE+b'\r'+TWO+b'\r\n'+b'KNX[5]: CC\r'+THREE
                    raw_parts.append(chunk)
                    for i in range(0,len(chunk),13):
                        conn.sendall(chunk[i:i+13])
                        time.sleep(.009)
                    # Final frame has no CR/LF: should be flushed after idle.
                    conn.settimeout(5)
                    while True:
                        data=conn.recv(4096)
                        if m.EXIT_TCP in data:
                            conn.sendall(b'#ok')
                            break
                srv.close()
            th=threading.Thread(target=fake_gateway,daemon=True)
            th.start()
            c.settings['ip']='127.0.0.1'
            c.settings['port']=port
            c.start('CR-only test')
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and c.counts['frames']<3:
                time.sleep(.05)
            self.assertEqual(c.counts['frames'],3, c.snapshot())
            self.assertEqual(c.counts['acks'],1)
            self.assertGreater(c.counts['tcp_bytes'],len(raw_parts[0]))
            self.assertEqual(c.status,'running')
            c.add_note({'action':'OFF','zone':'Zona 1','origin':'Termostato'})
            c.stop()
            c.thread.join(6)
            self.assertEqual(c.status,'idle',c.snapshot())
            self.assertTrue(c.gateway_restored)
            raw=(c.session_path/'tcp_stream.bin').read_bytes()
            self.assertIn(raw_parts[0],raw)
            zdata,name=c.export()
            with zipfile.ZipFile(io.BytesIO(zdata)) as z:
                self.assertIn('tcp_stream.bin',z.namelist())
                ev=[json.loads(i) for i in z.read('eventi.jsonl').decode().splitlines()]
                self.assertEqual(len([i for i in ev if i['type']=='bus']),3)
                self.assertEqual(len([i for i in ev if i['type']=='note']),1)

if __name__=='__main__': unittest.main()