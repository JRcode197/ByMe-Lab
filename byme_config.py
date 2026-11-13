"""Validazione delle configurazioni locali, senza accesso al gateway."""
import copy
import hashlib
import json

MAX_JSON = 4_000_000


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def text(value, limit=500):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Testo non valido (massimo {limit} caratteri)")
    return value.strip()


def verified(value):
    if not isinstance(value, bool):
        raise ValueError("La verifica deve essere true o false")
    return value


def reference(raw, valid, kinds):
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get('kind') not in kinds or not isinstance(raw.get('address'), str):
        raise ValueError("Riferimento indirizzo non valido")
    if not valid(raw['kind'], raw['address']):
        raise ValueError("Indirizzo non valido: " + raw['address'])
    return {'kind': raw['kind'], 'address': raw['address']}


def normalize_zones(zones, valid):
    if not isinstance(zones, list) or not 1 <= len(zones) <= 100:
        raise ValueError("Configura da 1 a 100 zone indipendenti dalle uscite")
    result, ids, names = [], set(), set()
    for i, raw in enumerate(zones):
        if isinstance(raw, str):
            raw = {'id': f'zona-{i+1}', 'name': raw}
        if not isinstance(raw, dict):
            raise ValueError("Scheda zona non valida")
        zid, name = text(raw.get('id', ''), 80), text(raw.get('name', ''), 60)
        if not zid or not name or zid in ids or name.casefold() in names:
            raise ValueError("ID e nomi delle zone devono essere presenti e univoci")
        ids.add(zid)
        names.add(name.casefold())
        zone = {'id': zid, 'name': name}
        for key in ('thermostat', 'actuator'):
            device = raw.get(key) or {}
            if not isinstance(device, dict):
                raise ValueError("Associazione dispositivo non valida")
            zone[key] = {'model': '14514' if key == 'thermostat' else text(device.get('model', ''), 80),
                         'name': text(device.get('name', ''), 80),
                         'ref': reference(device.get('ref'), valid, ('sources', 'eeprom')),
                         'verified': verified(device.get('verified', False)),
                         'notes': text(device.get('notes', ''))}
        observed = raw.get('observed', [])
        if not isinstance(observed, list) or len(observed) > 256:
            raise ValueError("Elenco indirizzi di zona non valido")
        zone['observed'] = []
        seen = set()
        for item in observed:
            ref = reference(item, valid, ('sources', 'groups'))
            if ref is None or (ref['kind'], ref['address']) in seen:
                raise ValueError("Indirizzo di zona assente o duplicato")
            seen.add((ref['kind'], ref['address']))
            zone['observed'].append({**ref, 'verified': verified(item.get('verified', False)),
                                     'notes': text(item.get('notes', ''))})
        result.append(zone)
    return result


def normalize_inventory(raw, valid, categories):
    if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] not in (1, 2):
        raise ValueError("Formato non supportato: serve un inventario ByMe Lab v1 o v2")
    out = {'version': 2}
    for kind in ('sources', 'groups', 'eeprom'):
        records = raw.get(kind)
        if not isinstance(records, dict) or len(records) > 2048:
            raise ValueError(f"Sezione {kind} assente o non valida (massimo 2048 elementi)")
        out[kind] = {}
        for addr, item in records.items():
            if not valid(kind, addr) or not isinstance(item, dict):
                raise ValueError(f"Scheda {kind}/{addr} non valida")
            rec = {k: text(item.get(k, ''), n) for k, n in (('name', 80), ('zone', 80), ('notes', 500))}
            rec['category'] = item.get('category', 'sconosciuto')
            if rec['category'] not in categories:
                raise ValueError(f"Categoria non valida: {addr}")
            rec['verified'] = verified(item.get('verified', False))
            for key in ('count', 'repetitions'):
                value = item.get(key, 0)
                if type(value) is not int or not 0 <= value <= 10**12:
                    raise ValueError(f"Contatore non valido: {addr}")
                rec[key] = value
            if rec['repetitions'] > rec['count']:
                raise ValueError(f"Ritrasmissioni superiori al totale: {addr}")
            for key in ('first_seen', 'last_seen'):
                value = item.get(key)
                rec[key] = None if value is None else text(value, 64)
            links = item.get('links', {})
            if not isinstance(links, dict) or len(links) > 2048 or (kind == 'eeprom' and links):
                raise ValueError(f"Collegamenti non validi: {addr}")
            target = 'groups' if kind == 'sources' else 'sources'
            if any(not valid(target, a) or type(n) is not int or not 0 <= n <= 10**12 for a, n in links.items()):
                raise ValueError(f"Collegamenti non validi: {addr}")
            rec['links'] = copy.deepcopy(links)
            out[kind][addr] = rec
    imports = raw.get('imports', [])
    if not isinstance(imports, list) or len(imports) > 100 or any(not isinstance(x, str) or len(x) != 64 or any(c not in '0123456789abcdef' for c in x) for x in imports):
        raise ValueError("Cronologia importazioni non valida")
    out['imports'] = list(dict.fromkeys(imports))
    if 'climate_zones' in raw:
        out['climate_zones'] = normalize_zones(raw['climate_zones'], valid)
    return out


def plan_import(current, incoming, mode):
    if mode not in ('merge', 'replace'):
        raise ValueError("Scegli unione o sostituzione")
    result = copy.deepcopy(current)
    changes = []
    for kind in ('sources', 'groups', 'eeprom'):
        if mode == 'replace':
            result[kind] = copy.deepcopy(incoming[kind])
        else:
            for addr, rec in incoming[kind].items():
                old = current[kind].get(addr)
                merged = copy.deepcopy(rec)
                if old:
                    # Snapshot counters may overlap: never sum them.
                    for key in ('count', 'repetitions'):
                        merged[key] = max(old[key], rec[key])
                    for key, fn in (('first_seen', min), ('last_seen', max)):
                        dates = [x for x in (old[key], rec[key]) if x]
                        merged[key] = fn(dates) if dates else None
                    merged['links'] = dict(old['links'])
                    for a, n in rec['links'].items():
                        merged['links'][a] = max(n, merged['links'].get(a, 0))
                result[kind][addr] = merged
        if len(result[kind]) > 2048:
            raise ValueError(f"L'unione supera 2048 elementi in {kind}")
        for addr in sorted(set(current[kind]) | set(result[kind])):
            before, after = current[kind].get(addr), result[kind].get(addr)
            if before != after:
                changes.append({'kind': kind, 'address': addr, 'action': 'aggiunto' if before is None else 'rimosso' if after is None else 'modificato', 'before': before, 'after': after})
    if 'climate_zones' in incoming:
        zones = {z['id']: z for z in current.get('climate_zones', [])} if mode == 'merge' else {}
        zones.update({z['id']: copy.deepcopy(z) for z in incoming['climate_zones']})
        result['climate_zones'] = list(zones.values())
        if result['climate_zones'] != current.get('climate_zones'):
            changes.append({'kind': 'climate_zones', 'address': '', 'action': 'modificato', 'before': current.get('climate_zones'), 'after': result['climate_zones']})
    result['imports'] = list(dict.fromkeys(current.get('imports', []) + incoming['imports']))[-100:]
    result['version'] = 2
    return result, changes
