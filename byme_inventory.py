"""Registro locale e prudente degli indirizzi osservati in ESP_KNXGate.

Solo osservazione: una sorgente KNX, un indirizzo di gruppo e un ID EEPROM
NON sono necessariamente lo stesso dispositivo fisico. Nessun telegramma
viene inviato al bus da questo modulo.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import html
import io
import json
from pathlib import Path
import re
import threading
import time
import zipfile
import secrets
from byme_config import MAX_JSON, fingerprint, normalize_inventory, normalize_zones, plan_import

VERSION = 2
MAX_ZIP = 12_000_000
MAX_EVENTS_BYTES = 18_000_000
SOURCE_RE = re.compile(r"^(?:[0-9]|1[0-5])\.(?:[0-9]|1[0-5])\.(?:[0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$")
GROUP_RE = re.compile(r"^(?:[0-9]|[12][0-9]|3[01])/[0-7]/(?:[0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$")
EEPROM_RE = re.compile(r"^[A-F0-9]{4}$")
KINDS = ("sources", "groups", "eeprom")
CATEGORIES = ("sconosciuto", "clima", "luci", "tapparelle", "attuatori", "centrale", "sensori", "gateway", "altro")


def local_time():
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def parse_status(text):
    """Estrae l'elenco EEPROM da /status, senza presupporre un formato KNX standard."""
    cleaned = html.unescape(re.sub(r"<[^>]+>", " ", text))
    match = re.search(r"known\s+devices\s+in\s+eeprom\s*:\s*", cleaned, re.I)
    if not match:
        raise ValueError("Nello /status non compare 'known devices in eeprom'")
    tail = cleaned[match.end():]
    tail = re.split(r"\b(?:13\s*\.\s*ESP\s+mode|ESP\s+mode\s*:|14\s*\.\s*Free\s+heap)\b", tail, maxsplit=1, flags=re.I)[0]
    # Restrict tokens to four hexadecimal digits; reject status metrics and free text.
    ids = list(dict.fromkeys(x.upper() for x in re.findall(r"\b[A-Fa-f0-9]{4}\b", tail)))
    if not ids:
        raise ValueError("Nessun ID EEPROM di quattro cifre rilevato")
    return ids[:256]


class Inventory:
    """Persistente e thread safe; preferisce non inventare associazioni."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"version": VERSION, "sources": {}, "groups": {}, "eeprom": {}, "imports": []}
        self.changed = False
        self.last_save = 0.0
        self.pending_import = None
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for kind in KINDS:
                        if isinstance(raw.get(kind), dict):
                            self.data[kind] = raw[kind]
                    if isinstance(raw.get("imports"), list):
                        self.data["imports"] = raw["imports"][-100:]
                    if 'climate_zones' in raw:
                        self.data['climate_zones'] = normalize_zones(raw['climate_zones'], self.valid)
            except (OSError, ValueError):
                # Do not overwrite an invalid config until an explicit change.
                raise ValueError("Archivio dispositivi non valido: fai una copia e correggi dispositivi.json")

    @staticmethod
    def valid(kind, address):
        if kind == "sources":
            return bool(SOURCE_RE.fullmatch(address))
        if kind == "groups":
            return bool(GROUP_RE.fullmatch(address))
        return kind == "eeprom" and bool(EEPROM_RE.fullmatch(address))

    def _entry(self, kind, address, when=None):
        records = self.data[kind]
        if address not in records:
            if len(records) >= 2048:
                return None
            records[address] = {"name": "", "category": "sconosciuto", "zone": "", "notes": "", "verified": False,
                                "first_seen": when or local_time(), "last_seen": None,
                                "count": 0, "repetitions": 0, "links": {}}
            self.changed = True
        return records[address]

    def observe(self, event, *, autosave=True):
        """Accetta esclusivamente i campi di una riga `bus` già interpretata."""
        if event.get("type") != "bus":
            return False
        src = str(event.get("src", ""))
        dst = str(event.get("dst", ""))
        when = str(event.get("time") or local_time())[:64]
        src_ok = self.valid("sources", src)
        dst_ok = self.valid("groups", dst) and event.get("dst_type", "gruppo") != "fisico"
        if not src_ok and not dst_ok:
            return False
        with self.lock:
            for kind, address, linked in (("sources", src, dst if dst_ok else None),
                                          ("groups", dst, src if src_ok else None)):
                if not self.valid(kind, address):
                    continue
                rec = self._entry(kind, address, when)
                if rec is None:
                    continue
                rec["count"] += 1
                rec["repetitions"] += int(bool(event.get("repeat")))
                rec["last_seen"] = when
                if linked:
                    rec["links"][linked] = rec["links"].get(linked, 0) + 1
                self.changed = True
            if autosave:
                self.save(throttled=True)
        return True

    def add_eeprom(self, ids):
        with self.lock:
            added = 0
            for identifier in ids:
                identifier = str(identifier).upper().strip()
                if not self.valid("eeprom", identifier):
                    continue
                if identifier not in self.data["eeprom"]:
                    added += 1
                rec = self._entry("eeprom", identifier)
                if rec:
                    rec["last_seen"] = local_time()
            self.changed = True
            self.save()
            return added

    def rename(self, kind, address, values):
        if kind not in KINDS:
            raise ValueError("Tipo indirizzo non supportato")
        address = str(address).strip()
        if kind == "eeprom":
            address = address.upper()
        if not self.valid(kind, address):
            raise ValueError("Indirizzo non valido")
        if not isinstance(values, dict):
            raise ValueError("Dati di modifica non validi")
        with self.lock:
            record = self._entry(kind, address)
            if record is None:
                raise ValueError("Limite di indirizzi raggiunto")
            for key, max_len in (("name", 80), ("zone", 80), ("notes", 500)):
                if key in values:
                    record[key] = str(values[key]).strip()[:max_len]
            if "category" in values:
                if values["category"] not in CATEGORIES:
                    raise ValueError("Categoria non valida")
                record["category"] = values["category"]
            if "verified" in values:
                if not isinstance(values["verified"], bool):
                    raise ValueError("Stato di verifica non valido")
                record["verified"] = values["verified"]
            self.changed = True
            self.save()
            return copy.deepcopy(record)

    def save(self, throttled=False):
        with self.lock:
            if not self.changed or (throttled and time.monotonic() - self.last_save < 3):
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)
            self.changed = False
            self.last_save = time.monotonic()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def set_zones(self, zones):
        zones = normalize_zones(zones, self.valid)
        with self.lock:
            updated = self.snapshot()
            updated['climate_zones'] = zones
            self._commit_config(updated)

    def _commit_config(self, updated):
        """Write backup and replacement before publishing the new in-memory state."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = self.path.with_name(self.path.stem + '.backup-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
        backup.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)
        self.data = updated
        self.changed = False
        self.last_save = time.monotonic()
        return str(backup)

    def preview_config(self, raw, mode):
        if len(json.dumps(raw, ensure_ascii=False).encode()) > MAX_JSON:
            raise ValueError('JSON troppo grande (massimo 4 MB)')
        incoming = normalize_inventory(raw, self.valid, CATEGORIES)
        with self.lock:
            updated, changes = plan_import(self.data, incoming, mode)
            if 'climate_zones' in updated:
                updated['climate_zones'] = normalize_zones(updated['climate_zones'], self.valid)
            preview_id = secrets.token_urlsafe(24)
            self.pending_import = (preview_id, fingerprint(self.data), updated, time.monotonic())
            return {'preview_id': preview_id, 'mode': mode, 'changes': changes,
                    'summary': {action: sum(c['action'] == action for c in changes) for action in ('aggiunto', 'modificato', 'rimosso')},
                    'zones_preserved': 'climate_zones' not in incoming}

    def apply_config(self, preview_id):
        with self.lock:
            pending = self.pending_import
            if not pending or pending[0] != preview_id or time.monotonic() - pending[3] > 900:
                raise ValueError('Anteprima assente o scaduta: rigenera l’anteprima')
            if pending[1] != fingerprint(self.data):
                raise ValueError('Il catalogo è cambiato: rigenera l’anteprima prima di importare')
            backup = self._commit_config(pending[2])
            self.pending_import = None
            return {'backup': backup}

    def label(self, kind, address):
        with self.lock:
            item = self.data.get(kind, {}).get(address, {})
            return item.get("name") or address

    def import_capture_zip(self, blob):
        """Indicizza una sessione esistente, senza copiare/modificare le sue note."""
        if not isinstance(blob, bytes) or len(blob) > MAX_ZIP or len(blob) < 50:
            raise ValueError("ZIP non valido o troppo grande (massimo 12 MB)")
        digest = hashlib.sha256(blob).hexdigest()
        with self.lock:
            if digest in self.data["imports"]:
                raise ValueError("Questo ZIP è già stato importato: nessun conteggio duplicato")
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                matches = [i for i in z.infolist() if i.filename == "eventi.jsonl"]
                if len(matches) != 1:
                    raise ValueError("Nello ZIP manca eventi.jsonl, o è duplicato")
                entry = matches[0]
                if entry.file_size > MAX_EVENTS_BYTES:
                    raise ValueError("eventi.jsonl troppo grande")
                # Max 40K events; do not trust a ZIP just because it has a familiar name.
                raw = z.read(entry, pwd=None)
                if len(raw) > MAX_EVENTS_BYTES:
                    raise ValueError("Archivio troppo grande")
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ValueError("ZIP danneggiato o illeggibile") from exc
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("eventi.jsonl non è UTF-8") from exc
        if len(lines) > 40000:
            raise ValueError("Troppi eventi nel file")
        observations = []
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("Riga JSON malformata nella sessione") from exc
            if isinstance(event, dict) and event.get("type") == "bus":
                observations.append(event)
        with self.lock:
            # Ignore repeated imports of this precise byte-identical archive.
            if digest in self.data["imports"]:
                raise ValueError("Questo ZIP è già stato importato")
            before = {k: len(self.data[k]) for k in KINDS}
            n = sum(self.observe(event, autosave=False) for event in observations)
            self.data["imports"].append(digest)
            self.data["imports"] = self.data["imports"][-100:]
            self.changed = True
            self.save()
            return {"frames": n, "new_sources": len(self.data["sources"]) - before["sources"],
                    "new_groups": len(self.data["groups"]) - before["groups"]}
