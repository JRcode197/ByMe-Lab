"""Test offline per censimento, EEPROM, importazione ZIP e salvataggio delle etichette."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('byme_inventory', ROOT / 'byme_inventory.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample_zip():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('eventi.jsonl', '\n'.join([
            json.dumps({'type': 'bus', 'time': '2026-11-12T12:00:00+01:00',
                        'src': '1.1.42', 'dst': '1/4/137', 'repeat': False}),
            json.dumps({'type': 'bus', 'time': '2026-11-12T12:00:01+01:00',
                        'src': '1.1.42', 'dst': '1/4/137', 'repeat': True}),
            json.dumps({'type': 'note', 'action': 'OFF', 'zone': 'Zona 1'}),
        ]))
    return stream.getvalue()


class InventoryTests(unittest.TestCase):
    def test_live_observation_uses_separate_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            inv = m.Inventory(Path(directory) / 'dispositivi.json')
            inv.observe({'type':'bus','src':'1.1.42','dst':'1/4/137', 'time':'2026-11-12T12:00:00+01:00','repeat':False})
            snap = inv.snapshot()
            self.assertIn('1.1.42', snap['sources'])
            self.assertIn('1/4/137', snap['groups'])
            self.assertEqual(snap['sources']['1.1.42']['links']['1/4/137'], 1)
            self.assertEqual(snap['groups']['1/4/137']['links']['1.1.42'], 1)
            self.assertEqual(snap['sources']['1.1.42']['name'], '')

    def test_import_and_no_double_import(self):
        with tempfile.TemporaryDirectory() as directory:
            inv = m.Inventory(Path(directory) / 'dispositivi.json')
            blob = sample_zip()
            result = inv.import_capture_zip(blob)
            self.assertEqual(result, {'frames':2,'new_sources':1,'new_groups':1})
            self.assertEqual(inv.snapshot()['groups']['1/4/137']['repetitions'], 1)
            with self.assertRaisesRegex(ValueError, 'già stato importato'):
                inv.import_capture_zip(blob)

    def test_alias_persists_without_changing_address(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dispositivi.json'
            inv = m.Inventory(path)
            inv.rename('groups','1/4/137',{'name':'Valvola camera (ipotetica)',
                                             'category':'clima','zone':'Camera',
                                             'notes':'Da confermare','verified':False})
            again = m.Inventory(path)
            self.assertEqual(again.label('groups','1/4/137'),'Valvola camera (ipotetica)')
            self.assertIn('1/4/137',again.snapshot()['groups'])
            with self.assertRaisesRegex(ValueError, 'Indirizzo non valido'):
                again.rename('groups','../../not-a-device',{'name':'no'})

    def test_eeprom_status_is_not_knx_group(self):
        gateway='12. known devices in eeprom: 0FA1 0FA9 0C89 0C91 13. ESP mode: KNX 14. Free heap: 12345'
        self.assertEqual(m.parse_status(gateway),['0FA1','0FA9','0C89','0C91'])
        with tempfile.TemporaryDirectory() as directory:
            inv=m.Inventory(Path(directory)/'dispositivi.json')
            self.assertEqual(inv.add_eeprom(m.parse_status(gateway)),4)
            self.assertEqual(len(inv.snapshot()['groups']),0)
            self.assertEqual(len(inv.snapshot()['sources']),0)
            self.assertEqual(len(inv.snapshot()['eeprom']),4)

    def test_import_limits_and_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            inv=m.Inventory(Path(directory)/'dispositivi.json')
            with self.assertRaises(ValueError):inv.import_capture_zip(b'not-a-zip')
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:z.writestr('../eventi.jsonl','{}')
            with self.assertRaisesRegex(ValueError,'eventi.jsonl'):
                inv.import_capture_zip(stream.getvalue())

if __name__ == '__main__':unittest.main()