"""Importazione transazionale e zone indipendenti; nessun gateway reale."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import http.client
import threading
from unittest.mock import patch

from byme_config import normalize_zones
from byme_inventory import Inventory


def config():
    return {'version': 1, 'sources': {}, 'groups': {}, 'eeprom': {
        '0C89': {'name': 'Termostato candidato', 'category': 'clima', 'verified': False}}, 'imports': []}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inv = Inventory(Path(self.tmp.name) / 'dispositivi.json')
        self.inv.set_zones([f'Zona {i}' for i in range(1, 6)])

    def test_preview_does_not_write_and_apply_backups_and_roundtrips(self):
        before = self.inv.snapshot()
        disk = self.inv.path.read_bytes()
        preview = self.inv.preview_config(config(), 'merge')
        self.assertEqual(before, self.inv.snapshot())
        self.assertEqual(disk, self.inv.path.read_bytes())
        self.assertEqual(preview['summary']['aggiunto'], 1)
        result = self.inv.apply_config(preview['preview_id'])
        self.assertEqual(json.loads(Path(result['backup']).read_text(encoding='utf-8')), before)
        self.assertEqual(Inventory(self.inv.path).snapshot(), self.inv.snapshot())
        self.assertFalse(self.inv.data['eeprom']['0C89']['verified'])
        self.assertEqual(len(self.inv.data['climate_zones']), 5)
        self.assertEqual(self.inv.preview_config(self.inv.snapshot(), 'replace')['changes'], [])
        with self.assertRaises(ValueError):
            self.inv.apply_config(preview['preview_id'])

    def test_merge_preserves_observations_replace_lists_removals(self):
        self.inv.observe({'type': 'bus', 'src': '1.1.42', 'dst': '1/4/137'})
        incoming = self.inv.snapshot()
        incoming['sources']['1.1.42'].update(name='Nome importato', count=0, links={})
        incoming['groups'] = {}
        preview = self.inv.preview_config(incoming, 'merge')
        self.inv.apply_config(preview['preview_id'])
        self.assertEqual(self.inv.data['sources']['1.1.42']['count'], 1)
        self.assertEqual(self.inv.data['sources']['1.1.42']['links'], {'1/4/137': 1})
        self.assertIn('1/4/137', self.inv.data['groups'])
        self.assertEqual(self.inv.preview_config(incoming, 'merge')['changes'], [])
        preview = self.inv.preview_config(config(), 'replace')
        self.assertEqual(preview['summary']['rimosso'], 2)
        self.inv.apply_config(preview['preview_id'])
        self.assertEqual(self.inv.data['sources'], {})
        self.assertEqual(len(self.inv.data['climate_zones']), 5)

    def test_concurrent_change_invalidates_preview(self):
        preview = self.inv.preview_config(config(), 'merge')
        self.inv.rename('eeprom', '0C91', {'name': 'Nuovo'})
        with self.assertRaisesRegex(ValueError, 'cambiato'):
            self.inv.apply_config(preview['preview_id'])
        self.assertNotIn('0C89', self.inv.data['eeprom'])

    def test_write_failure_leaves_state_and_file_unchanged(self):
        preview = self.inv.preview_config(config(), 'replace')
        before, disk = self.inv.snapshot(), self.inv.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.inv.apply_config(preview['preview_id'])
        self.assertEqual(self.inv.snapshot(), before)
        self.assertEqual(self.inv.path.read_bytes(), disk)

    def test_invalid_records_are_rejected_without_partial_import(self):
        for field, value in [('verified', 'false'), ('count', -1), ('count', True),
                             ('links', {'1.1.42': 1}), ('category', 'invalid')]:
            with self.subTest(field=field, value=value):
                raw = config()
                raw['eeprom']['0C89'][field] = value
                with self.assertRaises(ValueError):
                    self.inv.preview_config(raw, 'merge')
        for raw in ({}, [], {**config(), 'version': 99}, {**config(), 'sources': []}):
            with self.assertRaises(ValueError):
                self.inv.preview_config(raw, 'merge')
        self.assertEqual(self.inv.data['eeprom'], {})

    def test_zone_associations_have_separate_verification(self):
        zones = normalize_zones([f'Zona {i}' for i in range(8)], self.inv.valid)
        zones[4]['thermostat'].update(ref={'kind': 'eeprom', 'address': '0C89'}, verified=True)
        zones[4]['actuator'].update(name='Attuatore A', model='01851.2', verified=False)
        zones[4]['observed'] = [{'kind': 'groups', 'address': '1/4/137', 'verified': False, 'notes': 'Da provare'}]
        self.inv.set_zones(zones)
        again = Inventory(self.inv.path).snapshot()['climate_zones']
        self.assertEqual(again, zones)
        self.assertTrue(again[4]['thermostat']['verified'])
        self.assertFalse(again[4]['actuator']['verified'])
        self.assertFalse(again[4]['observed'][0]['verified'])
        self.assertEqual(self.inv.data['groups'], {})  # Mapping is not an observation.

    def test_zone_merge_by_id_and_replace(self):
        raw = config()
        raw['climate_zones'] = [copy.deepcopy(self.inv.data['climate_zones'][0])]
        raw['climate_zones'][0]['name'] = 'Nome nuovo'
        preview = self.inv.preview_config(raw, 'merge')
        self.inv.apply_config(preview['preview_id'])
        self.assertEqual(len(self.inv.data['climate_zones']), 5)
        self.assertEqual(self.inv.data['climate_zones'][0]['name'], 'Nome nuovo')
        preview = self.inv.preview_config(raw, 'replace')
        self.inv.apply_config(preview['preview_id'])
        self.assertEqual(len(self.inv.data['climate_zones']), 1)

    def test_invalid_zone_references_and_duplicates(self):
        zones = copy.deepcopy(self.inv.data['climate_zones'])
        zones[0]['thermostat']['ref'] = {'kind': 'groups', 'address': '1/4/137'}
        with self.assertRaises(ValueError):
            self.inv.set_zones(zones)
        with self.assertRaises(ValueError):
            self.inv.set_zones(['Camera', 'camera'])
        with self.assertRaises(ValueError):
            self.inv.set_zones([])


class ConfigurationApiTests(unittest.TestCase):
    def setUp(self):
        import byme_lab
        self.app = byme_lab
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / 'impostazioni.json').write_text(json.dumps({'zones': ['Uno', 'Due', 'Tre', 'Quattro']}), encoding='utf-8')
        self.patches = [patch.object(byme_lab, 'BASE_DIR', base), patch.object(byme_lab, 'SESSIONS', base / 'sessioni'), patch.object(byme_lab, 'SETTINGS_FILE', base / 'impostazioni.json')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.capture = byme_lab.Capture()
        p = patch.object(byme_lab, 'CAPTURE', self.capture)
        p.start()
        self.addCleanup(p.stop)
        self.server = byme_lab.ThreadingHTTPServer(('127.0.0.1', 0), byme_lab.Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def post(self, path, data, token=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Byme-Token'] = self.app.TOKEN
        conn.request('POST', path, json.dumps(data), headers)
        response = conn.getresponse()
        status, result = response.status, json.loads(response.read())
        conn.close()
        return status, result

    def test_migration_fifth_zone_and_six_zone_save(self):
        self.assertEqual(self.capture.settings['zones'], ['Uno', 'Due', 'Tre', 'Quattro', 'Zona 5'])
        zones = copy.deepcopy(self.capture.settings['climate_zones'])
        zones.append({'id': 'sixth', 'name': 'Sei'})
        status, _ = self.post('/api/zones', {'climate_zones': zones})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.app.Capture().settings['zones']), 6)

    def test_api_requires_token_and_idle_then_import_syncs_zones(self):
        raw = config()
        raw['climate_zones'] = [{'id': 'test', 'name': 'Zona importata'}]
        status, _ = self.post('/api/inventory/import-preview', {'config': raw, 'mode': 'replace'}, token=False)
        self.assertEqual(status, 403)
        self.capture.status = 'running'
        status, _ = self.post('/api/inventory/import-preview', {'config': raw, 'mode': 'replace'})
        self.assertEqual(status, 400)
        self.capture.status = 'idle'
        status, preview = self.post('/api/inventory/import-preview', {'config': raw, 'mode': 'replace'})
        self.assertEqual(status, 200)
        status, _ = self.post('/api/inventory/import-apply', {'preview_id': preview['preview_id']})
        self.assertEqual(status, 200)
        self.assertEqual(self.capture.settings['zones'], ['Zona importata'])
        self.assertEqual(self.app.Capture().settings['zones'], ['Zona importata'])


if __name__ == '__main__':
    unittest.main()
