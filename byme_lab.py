#!/usr/bin/env python3
"""ByMe Lab — acquisizione passiva e annotazione per ESP_KNXGate.

Python 3.9+; soltanto libreria standard. Web UI accessibile unicamente da localhost.
Non trasmette MAI telegrammi di comando agli attuatori By-me.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.1.0"
BASE_DIR = Path.home() / "Documents" / "ByMe-Lab"
SESSIONS = BASE_DIR / "sessioni"
SETTINGS_FILE = BASE_DIR / "impostazioni.json"
STATIC = Path(__file__).resolve().parent / "static"
ENTER_TCP = b'#setup {"debug":"no","uart":"tcp"}'
MONITOR_CMD = b'@\x15@MA@F0@Y0@h@l@\x16'
EXIT_TCP = b'#setup {"debug":"no"}'
KNX_RE = re.compile(r"^\s*(KNX|SCS)\[(\d+)\]:\s*(.*)$", re.I)
HEX_RE = re.compile(r"^[0-9a-fA-F]{2}$")
DEFAULTS = {"ip": "192.168.21.113", "port": 5045,
            "zones": ["Zona 1", "Zona 2", "Zona 3", "Zona 4"]}


def timestamp():
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def safe_name(text, fallback="test"):
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(text).strip())[:45].strip("_")
    return text or fallback


def phy_address(value):
    return f"{(value >> 12) & 0x0F}.{(value >> 8) & 0x0F}.{value & 255}"


def group_address(value):
    return f"{(value >> 11) & 31}/{(value >> 8) & 7}/{value & 255}"


def decode_bus_line(line):
    """Decodifica solo i campi standard KNX; il significato By-me resta ignoto."""
    result = {"raw": line, "kind": "info"}
    match = KNX_RE.match(line.strip())
    if not match:
        return result
    proto, index, content = match.groups()
    tokens = content.strip().split()
    result.update(kind="bus", protocol=proto.upper(), slot=int(index), hex=content.strip().upper())
    if not tokens or not all(HEX_RE.fullmatch(t) for t in tokens):
        result.update(kind="info", note="Riga bus non interamente esadecimale")
        return result
    data = bytes(int(t, 16) for t in tokens)
    result["bytes"] = list(data)
    result["nbytes"] = len(data)
    if len(data) == 1 and data[0] in (0xCC, 0x0C):
        result["kind"] = "ack"
        result["meaning"] = "Conferma / segnale breve (interpretazione cauta)"
        return result
    if proto.upper() != "KNX" or len(data) < 9:
        return result
    src, dst = int.from_bytes(data[1:3], "big"), int.from_bytes(data[3:5], "big")
    group = bool(data[5] & 0x80)
    expected = 8 + (data[5] & 0x0F)
    valid_length = expected == len(data)
    valid_checksum = (sum_xor(data) == 0xFF)
    apci = ((data[6] & 3) << 2) | (data[7] >> 6)
    op = {0: "GroupValueRead", 1: "GroupValueResponse", 2: "GroupValueWrite"}.get(apci, f"APCI {apci}")
    payload = data[8:-1]
    is_repeat = not bool(data[0] & 0x20)
    result.update({"src": phy_address(src), "dst": group_address(dst) if group else phy_address(dst),
                   "dst_type": "gruppo" if group else "fisico", "apci": op,
                   "payload_hex": payload.hex(" ").upper(),
                   "short_value": (data[7] & 0x3F) if not payload and apci in (1, 2) else None,
                   "repeat": is_repeat,
                   "checksum_ok": valid_checksum, "length_ok": valid_length,
                   "expected_nbytes": expected})
    if apci in (1, 2) and not payload:
        result["value"] = str(data[7] & 0x3F)
    elif apci in (1, 2) and len(payload) == 1:
        result["value"] = str(payload[0])
    elif payload:
        result["value"] = payload.hex(" ").upper()
    else:
        result["value"] = "—"
    if not valid_length or not valid_checksum:
        result["warning"] = "Verificare: lunghezza o checksum non conforme a KNX standard"
    return result


def sum_xor(seq):
    out = 0
    for b in seq:
        out ^= b
    return out


class StreamFramer:
    """Framing robusto per LF, CRLF, CR e porzioni senza terminatore."""

    def __init__(self, callback):
        self.callback = callback
        self.pending = ""

    def feed(self, text):
        self.pending += text
        while True:
            match = re.search(r"\r\n|\r|\n", self.pending)
            if not match:
                break
            raw = self.pending[:match.start()]
            self.pending = self.pending[match.end():]
            if raw.strip():
                self.callback(raw)
        # Defensive bound for malformed/unbroken streams: do not lose bytes.
        while len(self.pending) > 16000:
            self.callback(self.pending[:16000])
            self.pending = self.pending[16000:]

    def flush(self):
        if self.pending.strip():
            self.callback(self.pending)
        self.pending = ""


class Capture:
    def __init__(self):
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        SESSIONS.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.settings = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
        self.status = "idle"
        self.message = "Pronto: avvia una nuova acquisizione"
        self.stop_signal = threading.Event()
        self.thread = None
        self.events = collections.deque(maxlen=7000)
        self.seq = 0
        self.counts = {"frames": 0, "repeats": 0, "acks": 0, "infos": 0, "notes": 0, "ignored": 0, "bad": 0, "tcp_bytes": 0, "tcp_chunks": 0, "monitor_bytes": 0, "monitor_chunks": 0, "lines": 0}
        self.groups = collections.Counter()
        self.sources = collections.Counter()
        self.session_path = None
        self.session_name = ""
        self.session_started = None
        self.jsonlf = None
        self.rawf = None
        self.streamf = None
        self.pending_chars = 0
        self.last_rx = None
        self.gateway_restored = None
        self.paused_at = None

    def emit(self, typ, **fields):
        with self.lock:
            self.seq += 1
            event = {"id": self.seq, "time": timestamp(), "type": typ, **fields}
            self.events.append(event)
            if self.jsonlf:
                self.jsonlf.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.jsonlf.flush()
            if typ in ("bus", "ack", "info") and self.rawf:
                self.rawf.write(f"{event['time']} | {fields.get('raw', '')}\n")
                self.rawf.flush()
            if typ in ("bus", "ack", "info"):
                self.counts["lines"] += 1
            if typ == "bus":
                self.counts["frames"] += 1
                if fields.get("repeat"):
                    self.counts["repeats"] += 1
                if fields.get("checksum_ok") is False or fields.get("length_ok") is False:
                    self.counts["bad"] += 1
                if fields.get("dst"):
                    self.groups[fields["dst"]] += 1
                if fields.get("src"):
                    self.sources[fields["src"]] += 1
            elif typ == "ack":
                self.counts["acks"] += 1
            elif typ == "info":
                self.counts["infos"] += 1
            elif typ == "note":
                self.counts["notes"] += 1
            return event

    def set_settings(self, data):
        ip = str(data.get("ip", "")).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,150}", ip):
            raise ValueError("Indirizzo IP o hostname non valido")
        port = int(data.get("port", 5045))
        if not 1 <= port <= 65535:
            raise ValueError("Porta non valida")
        zones = data.get("zones", DEFAULTS["zones"])
        if not isinstance(zones, list) or len(zones) != 4:
            raise ValueError("Servono quattro nomi di zona")
        zones = [str(x).strip()[:60] or f"Zona {i+1}" for i, x in enumerate(zones)]
        with self.lock:
            if self.status in ("running", "paused", "connecting", "stopping"):
                raise ValueError("Arresta il monitor prima di cambiare il gateway")
            self.settings = {"ip": ip, "port": port, "zones": zones}
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.settings

    def start(self, name):
        with self.lock:
            if self.status in ("running", "paused", "connecting", "stopping"):
                raise ValueError("Monitor già attivo")
            now = dt.datetime.now()
            base = f"{now:%Y%m%d_%H%M%S}_{safe_name(name)}"
            folder = SESSIONS / base
            suffix = 2
            while folder.exists():
                folder = SESSIONS / f"{base}_{suffix}"
                suffix += 1
            folder.mkdir(parents=True, exist_ok=False)
            self.session_path = folder
            self.session_name = str(name).strip()[:90] or "Prova termostati"
            self.session_started = timestamp()
            self.events.clear()
            self.seq = 0
            self.counts = {k: 0 for k in self.counts}
            self.groups.clear()
            self.sources.clear()
            self.stop_signal.clear()
            self.gateway_restored = None
            self.paused_at = None
            self.pending_chars = 0
            self.last_rx = None
            self.jsonlf = (folder / "eventi.jsonl").open("w", encoding="utf-8", buffering=1)
            self.rawf = (folder / "bus_raw.txt").open("w", encoding="utf-8", buffering=1)
            self.streamf = (folder / "tcp_stream.bin").open("wb", buffering=0)
            (folder / "sessione.json").write_text(json.dumps({
                "versione": VERSION, "sessione": self.session_name, "inizio": self.session_started,
                "gateway": {"ip": self.settings["ip"], "port": self.settings["port"]},
                "zone": self.settings["zones"], "impianto": {
                    "centrale": "Vimar 21509", "termostati": "4 × Vimar 14514",
                    "attuatore": "Vimar 01851.2", "gateway": "ESP_KNXGate"}},
                ensure_ascii=False, indent=2), encoding="utf-8")
            self.status = "connecting"
            self.message = "Connessione al gateway…"
            self.emit("system", message=f"Sessione avviata: {self.session_name}")
            self.thread = threading.Thread(target=self._reader, name="ESP-KNXGate", daemon=True)
            self.thread.start()

    def pause(self):
        with self.lock:
            if self.status != "running":
                raise ValueError("La pausa è disponibile solo durante l'acquisizione")
            self.status = "paused"
            self.paused_at = timestamp()
            self.message = "Pausa: i pacchetti in arrivo vengono scartati, senza interrompere TCP"
            self.emit("system", message="PAUSA acquisizione: inizio intervallo non registrato")

    def resume(self):
        with self.lock:
            if self.status != "paused":
                raise ValueError("Acquisizione non in pausa")
            self.status = "running"
            self.message = "Acquisizione in corso"
            self.emit("system", message=f"RIPRESA acquisizione (pausa iniziata: {self.paused_at})")
            self.paused_at = None

    def stop(self):
        with self.lock:
            if self.status not in ("running", "paused", "connecting"):
                raise ValueError("Nessuna acquisizione da interrompere")
            self.status = "stopping"
            self.message = "Interruzione e ripristino del gateway…"
            self.emit("system", message="Arresto richiesto")
            self.stop_signal.set()

    def add_note(self, data):
        with self.lock:
            if self.status not in ("running", "paused"):
                raise ValueError("Avvia il monitor prima di annotare una prova")
            action = str(data.get("action", "NOTA")).strip()[:80]
            zone = str(data.get("zone", "")).strip()[:60]
            origin = str(data.get("origin", "")).strip()[:60]
            note = str(data.get("note", "")).strip()[:2000]
            if not any((action, zone, origin, note)):
                raise ValueError("Nota vuota")
            return self.emit("note", action=action, zone=zone, origin=origin, note=note)

    def _receive(self, chunk, monitor=False):
        """Keep the exact TCP bytes even when parsing/line endings fail."""
        with self.lock:
            self.counts["tcp_bytes"] += len(chunk)
            self.counts["tcp_chunks"] += 1
            if monitor:
                self.counts["monitor_bytes"] += len(chunk)
                self.counts["monitor_chunks"] += 1
            self.last_rx = timestamp()
            if self.streamf:
                self.streamf.write(chunk)

    def _reader(self):
        sock = None
        connected = False
        framer = StreamFramer(self._capture_line)
        last_data = time.monotonic()
        try:
            with self.lock:
                ip, port = self.settings["ip"], self.settings["port"]
            sock = socket.create_connection((ip, port), timeout=7)
            sock.settimeout(0.3)
            connected = True
            self.emit("system", message=f"TCP connesso a {ip}:{port}")
            sock.sendall(ENTER_TCP)
            handshake = b""
            until = time.monotonic() + 5
            while b"#ok" not in handshake and time.monotonic() < until and not self.stop_signal.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("Il gateway ha chiuso la connessione durante il setup")
                self._receive(chunk)
                handshake += chunk
                if len(handshake) > 32768:
                    raise ConnectionError("Risposta setup troppo lunga o senza #ok")
                if b"#err" in handshake and b"#ok" not in handshake:
                    raise ConnectionError("Gateway ha risposto #err al comando #setup")
            if b"#ok" not in handshake:
                raise TimeoutError("Il gateway non ha confermato #setup con #ok")
            self.emit("system", message="Configurazione TCP accettata: #ok")
            sock.sendall(MONITOR_CMD)
            with self.lock:
                if self.status == "connecting":
                    self.status = "running"
                    self.message = "Acquisizione in corso"
            self.emit("system", message="Monitor avviato: acquisizione byte TCP attiva")
            tail = handshake.split(b"#ok", 1)[1]
            if tail:
                with self.lock:
                    self.counts["monitor_bytes"] += len(tail)
                framer.feed(tail.decode("latin-1"))
                last_data = time.monotonic()
            while not self.stop_signal.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    # ESP_KNXGate can emit CR-only and even un-terminated lines.
                    # Flush an idle fragment, never hold the capture until Stop.
                    if framer.pending and time.monotonic() - last_data > 0.8:
                        framer.flush()
                        with self.lock:
                            self.pending_chars = 0
                    continue
                if not chunk:
                    raise ConnectionError("Gateway ha chiuso la connessione")
                self._receive(chunk, monitor=True)
                last_data = time.monotonic()
                framer.feed(chunk.decode("latin-1"))
                with self.lock:
                    self.pending_chars = len(framer.pending)
            framer.flush()
        except (OSError, ConnectionError, TimeoutError, ValueError) as exc:
            framer.flush()
            with self.lock:
                self.message = f"Errore: {exc}"
            self.emit("system", message=f"ERRORE connessione: {exc}")
        finally:
            restored = None
            if connected and sock:
                try:
                    sock.sendall(EXIT_TCP)
                    reply = b""
                    end = time.monotonic() + 2
                    while time.monotonic() < end and b"#ok" not in reply:
                        try:
                            part = sock.recv(4096)
                        except socket.timeout:
                            continue
                        if not part:
                            break
                        self._receive(part)
                        reply += part
                        if len(reply) > 65536:
                            reply = reply[-65536:]
                    restored = b"#ok" in reply
                except OSError:
                    restored = False
                try:
                    sock.close()
                except OSError:
                    pass
            with self.lock:
                self.gateway_restored = restored
                self.pending_chars = 0
                if restored is True:
                    self.emit("system", message="Ripristino modalità TCP richiesto, #ok ricevuto")
                elif connected:
                    self.emit("system", message="ATTENZIONE: ripristino TCP non confermato; verifica /status")
                self.emit("system", message="Acquisizione terminata")
                if self.session_path:
                    (self.session_path / "statistiche_finali.json").write_text(json.dumps({
                        "counts": self.counts, "gateway_restored": self.gateway_restored,
                        "fine": timestamp()}, ensure_ascii=False, indent=2), encoding="utf-8")
                for attr in ("jsonlf", "rawf", "streamf"):
                    f = getattr(self, attr)
                    if f:
                        f.close()
                        setattr(self, attr, None)
                had_error = self.message.startswith("Errore:")
                self.status = "error" if had_error else "idle"
                if not had_error:
                    self.message = "Acquisizione terminata, pronto per una nuova prova"
                self.thread = None

    def _capture_line(self, raw):
        if not raw.strip():
            return
        with self.lock:
            if self.status == "paused":
                self.counts["ignored"] += 1
                return
        parsed = decode_bus_line(raw)
        self.emit(parsed.pop("kind"), **parsed)

    def snapshot(self, since=0):
        with self.lock:
            selected = [e for e in self.events if e["id"] > since]
            if len(selected) > 600:
                selected = selected[:600]
            return {"version": VERSION, "status": self.status, "message": self.message,
                    "settings": self.settings, "counts": dict(self.counts),
                    "groups": self.groups.most_common(12), "sources": self.sources.most_common(12),
                    "session": str(self.session_path) if self.session_path else None,
                    "session_name": self.session_name,
                    "started": self.session_started,
                    "gateway_restored": self.gateway_restored,
                    "pending_chars": self.pending_chars, "last_rx": self.last_rx,
                    "events": selected, "latest_id": self.seq,
                    "oldest_id": self.events[0]["id"] if self.events else 0}

    def available_sessions(self):
        folders = sorted((p for p in SESSIONS.iterdir() if p.is_dir() and (p / "sessione.json").exists()),
                         key=lambda p: p.name, reverse=True)[:100]
        records = []
        for folder in folders:
            try:
                metadata = json.loads((folder / "sessione.json").read_text(encoding="utf-8"))
                records.append({"id": folder.name, "name": metadata.get("sessione", folder.name),
                                "start": metadata.get("inizio", "")})
            except (OSError, ValueError):
                continue
        return records

    def export(self, archive=None):
        with self.lock:
            if archive:
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,110}", archive):
                    raise ValueError("Nome di sessione non valido")
                folder = SESSIONS / archive
                if not folder.is_dir() or not (folder / "sessione.json").exists():
                    raise ValueError("Sessione non trovata")
            else:
                folder = self.session_path
            if not folder:
                raise ValueError("Non ci sono sessioni da esportare")
            if folder == self.session_path:
                if self.jsonlf:
                    self.jsonlf.flush()
                if self.rawf:
                    self.rawf.flush()
            events_path = folder / "eventi.jsonl"
            events = []
            with events_path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            settings = json.loads((folder / "sessione.json").read_text(encoding="utf-8"))
            notes = [e for e in events if e["type"] == "note"]
            frames = [e for e in events if e["type"] == "bus"]
            groups = collections.Counter(e.get("dst", "?") for e in frames)
            srcs = collections.Counter(e.get("src", "?") for e in frames)
            try:
                final_stats = json.loads((folder / "statistiche_finali.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                final_stats = {"counts": dict(self.counts) if folder == self.session_path else {},
                               "gateway_restored": self.gateway_restored if folder == self.session_path else None}
            summary = {"sessione": settings, "export_date": timestamp(), "state_at_export": (self.status if folder == self.session_path else "archived"),
                       "total_frames": len(frames), "total_notes": len(notes),
                       "retransmissions": sum(bool(e.get("repeat")) for e in frames),
                       "groups": groups.most_common(), "sources": srcs.most_common(),
                       "dropped_during_pause": final_stats.get("counts", {}).get("ignored", None),
                       "gateway_restored": final_stats.get("gateway_restored"),
                       "warnings": ["Interpretazioni DPT/By-me non confermate; valori grezzi preservati.",
                                    "Finestre eventi 12s dopo le note: correlazione, NON causalità.",
                                    "La pausa acquisizione scarta telegrammi; non usare una pausa per prove importanti."]}
            experiments = []
            for i, n in enumerate(notes):
                start = dt.datetime.fromisoformat(n["time"])
                end = start + dt.timedelta(seconds=12)
                if i + 1 < len(notes):
                    next_note = dt.datetime.fromisoformat(notes[i+1]["time"])
                    end = min(end, next_note)
                candidates = collections.defaultdict(lambda: {"count": 0, "values": [], "sources": set()})
                for ev in frames:
                    t = dt.datetime.fromisoformat(ev["time"])
                    if start <= t < end and not ev.get("repeat"):
                        dest = ev.get("dst", "sconosciuta")
                        c = candidates[dest]
                        c["count"] += 1
                        c["sources"].add(ev.get("src", "?"))
                        value = ev.get("value", "")
                        if value not in c["values"]:
                            c["values"].append(value)
                experiments.append({"nota": n, "finestra_fino_a": end.isoformat(),
                                    "gruppi_osservati": [{"dst": dst, "count": c["count"],
                                                         "values": c["values"], "sources": sorted(c["sources"])}
                                                        for dst, c in sorted(candidates.items(), key=lambda kv: -kv[1]["count"]) ]})
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for filename in ("eventi.jsonl", "bus_raw.txt", "tcp_stream.bin", "sessione.json"):
                    z.write(folder / filename, arcname=filename)
                z.writestr("riepilogo.json", json.dumps(summary, ensure_ascii=False, indent=2))
                z.writestr("esperimenti.json", json.dumps(experiments, ensure_ascii=False, indent=2))
                csvbuf = io.StringIO()
                writer = csv.writer(csvbuf)
                writer.writerow(["ora_locale", "tipo", "sorgente", "destinazione", "operazione", "valore", "ritrasmissione", "checksum_ok", "telegramma_grezzo"])
                for ev in frames:
                    writer.writerow([ev.get("time"), ev["type"], ev.get("src", ""), ev.get("dst", ""),
                                     ev.get("apci", ""), ev.get("value", ""), ev.get("repeat", ""),
                                     ev.get("checksum_ok", ""), ev.get("raw", "")])
                z.writestr("telegrammi.csv", "\ufeff" + csvbuf.getvalue())
                textbuf = ["NOTE CRONOLOGICHE DELLE PROVE", "--------------------------------", ""]
                for n in notes:
                    textbuf += [f"{n['time']} | {n.get('zone','')} | {n.get('origin','')} | {n.get('action','')}",
                                f"  {n.get('note', '')}", ""]
                z.writestr("note_esperimento.txt", "\n".join(textbuf))
                z.writestr("LEGGIMI_ANALISI.txt", (
                    "ByMe Lab — dati da inviare a ChatGPT per l'analisi.\n\n"
                    "Invia direttamente questo ZIP in chat e specifica quali gesti hai eseguito.\n"
                    "eventi.jsonl: sequenza cronologica completa con timestamp e note.\n"
                    "bus_raw.txt: righe del gateway + orari locali.\n"
                    "tcp_stream.bin: tutti i byte TCP ricevuti, anche se il parser fallisce.\n"
                    "telegrammi.csv: campi standard KNX interpretati ove possibile.\n"
                    "esperimenti.json: telegrammi osservati 12 secondi dopo una nota.\n"
                    "ATTENZIONE: gruppo / DPT / semantica Vimar By-me ancora da confermare.\n"
                    "Il monitor NON invia comandi di funzionamento agli attuatori.\n"
                    "I log possono rivelare la struttura e le abitudini dell'impianto: condividili solo se desideri.\n"))
            return output.getvalue(), f"ByMe_Lab_{safe_name(settings.get('sessione', 'sessione'))}.zip"


CAPTURE = Capture()
TOKEN = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    server_version = "ByMeLab/" + VERSION

    def log_message(self, fmt, *args):
        # Evita la stampa delle chiamate di polling ripetitive.
        if not self.path.startswith("/api/state"):
            print("[web]", fmt % args)

    def respond_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/api/state":
            q = urllib.parse.parse_qs(parsed.query)
            try:
                since = max(0, int(q.get("since", ["0"])[0]))
            except ValueError:
                since = 0
            return self.respond_json(CAPTURE.snapshot(since))
        if path == "/api/sessions":
            if self.headers.get("X-Byme-Token") != TOKEN:
                return self.respond_json({"error": "Token di sicurezza assente"}, 403)
            return self.respond_json({"sessions": CAPTURE.available_sessions()})
        if path == "/api/export":
            if self.headers.get("X-Byme-Token") != TOKEN:
                return self.respond_json({"error": "Token di sicurezza assente"}, 403)
            try:
                query = urllib.parse.parse_qs(parsed.query)
                blob, name = CAPTURE.export(query.get("session", [None])[0])
            except ValueError as exc:
                return self.respond_json({"error": str(exc)}, 400)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(blob)
            except BrokenPipeError:
                pass
            return
        file_for = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        if path not in file_for:
            return self.respond_json({"error": "Not found"}, 404)
        data = (STATIC / file_for[path]).read_bytes()
        if path == "/":
            data = data.replace(b"__BYME_TOKEN__", TOKEN.encode("ascii"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if path == "/" else
                         ("application/javascript; charset=utf-8" if path.endswith("js") else "text/css; charset=utf-8"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.headers.get("X-Byme-Token") != TOKEN:
            return self.respond_json({"error": "Token di sicurezza assente"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 15000:
                return self.respond_json({"error": "Dimensione richiesta non valida"}, 400)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Richiesta JSON non valida")
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/start":
                CAPTURE.start(data.get("name", "Prova termostati"))
            elif path == "/api/stop":
                CAPTURE.stop()
            elif path == "/api/pause":
                CAPTURE.pause()
            elif path == "/api/resume":
                CAPTURE.resume()
            elif path == "/api/note":
                CAPTURE.add_note(data)
            elif path == "/api/settings":
                CAPTURE.set_settings(data)
            else:
                return self.respond_json({"error": "Endpoint inesistente"}, 404)
            return self.respond_json({"ok": True})
        except (ValueError, TypeError, OSError) as exc:
            return self.respond_json({"error": str(exc)}, 400)
        except json.JSONDecodeError:
            return self.respond_json({"error": "JSON malformato"}, 400)


def main():
    parser = argparse.ArgumentParser(description="ByMe Lab GUI, monitor passivo ESP_KNXGate")
    parser.add_argument("--ip", help="IP gateway, sovrascrive la preferenza")
    parser.add_argument("--no-browser", action="store_true", help="Non aprire il browser automaticamente")
    parser.add_argument("--web-port", type=int, default=8765, help="Porta dell'interfaccia web locale")
    args = parser.parse_args()
    if args.ip:
        CAPTURE.settings["ip"] = args.ip
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.web_port), Handler)
    except OSError as exc:
        print(f"Errore nell'avvio dell'interfaccia web: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"\nByMe Lab v{VERSION} — GUI locale: {url}")
    print(f"Log locali: {SESSIONS}")
    print("Premi Ctrl+C per terminare. Non chiudere il Terminale durante le prove.\n")
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nChiusura, attendo il ripristino del gateway…")
    finally:
        with CAPTURE.lock:
            if CAPTURE.status in ("running", "paused", "connecting"):
                CAPTURE.stop()
            worker = CAPTURE.thread
        if worker:
            worker.join(timeout=6)
        server.server_close()
        print("Programma terminato. Controlla /status sul gateway se il ripristino TCP non è confermato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())