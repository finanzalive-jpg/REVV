"""Rise Drop – invio e ricezione di file tra dispositivi vicini, senza internet.

Parla il protocollo aperto LocalSend v2 (https://github.com/localsend/protocol), quindi
funziona con l'app gratuita LocalSend su Windows, macOS, Android e iPhone e con gli altri PC Rise.

- scoperta: messaggi UDP multicast 224.0.0.167:53317 + richiesta HTTPS /register
- ricezione: server HTTPS sulla porta 53317, con certificato creato al primo avvio
- invio: /prepare-upload (l'altro dispositivo accetta o rifiuta) e poi /upload per ogni file
"""
import hashlib
import http.client
import json
import mimetypes
import os
import secrets
import socket
import ssl
import struct
import subprocess
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 53317
GROUP = '224.0.0.167'
API = '/api/localsend/v2'
CONF_DIR = os.path.expanduser('~/.config/rise/drop')
DOWNLOADS = os.path.expanduser('~/Download')


def _free_name(d, name):
    name = os.path.basename(name).replace('/', '_') or 'file'
    base, ext = os.path.splitext(name)
    cand, n = name, 2
    while os.path.exists(os.path.join(d, cand)):
        cand = f'{base} ({n}){ext}'
        n += 1
    return cand


def _insecure_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class Drop:
    def __init__(self, on_devices=None, on_request=None, on_event=None):
        self.on_devices = on_devices or (lambda d: None)
        self.on_request = on_request or (lambda r: None)
        self.on_event = on_event or (lambda e: None)
        os.makedirs(CONF_DIR, exist_ok=True)
        self.conf = self._load_conf()
        self.devices = {}          # fingerprint -> info
        self.pending = {}          # id richiesta -> {'event', 'accept'}
        self.session = None        # ricezione in corso
        self.lock = threading.Lock()
        self.fingerprint = ''
        self.ctx = None
        self.running = False

    # ---------- configurazione ----------
    def _load_conf(self):
        try:
            c = json.load(open(os.path.join(CONF_DIR, 'drop.json')))
        except Exception:
            c = {}
        c.setdefault('alias', 'Rise di ' + os.uname().nodename)
        c.setdefault('receive', True)
        return c

    def save_conf(self, alias=None, receive=None):
        if alias:
            self.conf['alias'] = str(alias)[:40]
        if receive is not None:
            self.conf['receive'] = bool(receive)
        json.dump(self.conf, open(os.path.join(CONF_DIR, 'drop.json'), 'w'))
        if self.running:
            self.announce()
        return dict(self.conf)

    def info(self, announce=None):
        d = {'alias': self.conf['alias'], 'version': '2.1', 'deviceModel': 'Rise', 'deviceType': 'desktop',
             'fingerprint': self.fingerprint, 'port': PORT, 'protocol': 'https', 'download': False}
        if announce is not None:
            d['announce'] = announce
        return d

    # ---------- avvio ----------
    def _cert(self):
        crt, key = os.path.join(CONF_DIR, 'cert.pem'), os.path.join(CONF_DIR, 'key.pem')
        if not (os.path.exists(crt) and os.path.exists(key)):
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
                            '-subj', '/CN=Rise Drop', '-keyout', key, '-out', crt],
                           capture_output=True, check=True)
            os.chmod(key, 0o600)
        der = ssl.PEM_cert_to_DER_cert(open(crt).read())
        self.fingerprint = hashlib.sha256(der).hexdigest().upper()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        return ctx

    def start(self):
        try:
            self.ctx = self._cert()
            srv = ThreadingHTTPServer(('0.0.0.0', PORT), _handler(self))
            srv.socket = self.ctx.wrap_socket(srv.socket, server_side=True)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self.udp.bind(('', PORT))
            self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            self.joined = set()
            self._join()   # se la rete non c'è ancora, ci si aggancia dopo (vedi _watch)
        except Exception as e:
            self.on_event({'type': 'error', 'message': 'Rise Drop non può partire: %s' % e})
            return False
        self.running = True
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._announcer, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        return True

    # ---------- rete: ci si aggancia a ogni scheda appena ha un indirizzo ----------
    @staticmethod
    def _addrs():
        try:
            out = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'scope', 'global'],
                                 capture_output=True, text=True, timeout=5).stdout
        except Exception:
            out = ''
        res = []
        if not out:   # senza il comando "ip": indirizzo principale, rete /24
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as t:
                    t.connect(('10.255.255.255', 1))
                    ip = t.getsockname()[0]
                if not ip.startswith('127.'):
                    res.append((ip, 24))
            except OSError:
                pass
            return res
        for l in out.splitlines():
            f = l.split()
            if 'inet' in f:
                ip, plen = f[f.index('inet') + 1].split('/')
                res.append((ip, int(plen)))
        return res

    def _join(self):
        now = {ip for ip, _ in self._addrs()}
        new = now - self.joined
        for ip in new:
            try:
                self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                    struct.pack('4s4s', socket.inet_aton(GROUP), socket.inet_aton(ip)))
            except OSError:
                pass
        self.joined = now
        return bool(new)

    def _watch(self):
        while True:
            time.sleep(5)
            try:
                if self._join():           # nuova rete (es. Wi‑Fi appena collegato): ci si presenta subito
                    for d in (0, 1, 3):
                        time.sleep(d)
                        self.announce()
                    threading.Thread(target=self.http_scan, daemon=True).start()
            except Exception:
                pass

    def http_scan(self):
        """Ricerca di riserva (come fa LocalSend): chiede a ogni indirizzo della rete locale chi è.
        Serve quando il router blocca i messaggi a tutti (multicast)."""
        targets = []
        for ip, plen in self._addrs():
            if plen < 22:                  # reti troppo grandi: si guarda solo il /24 vicino
                plen = 24
            base = struct.unpack('!I', socket.inet_aton(ip))[0]
            mask = (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF
            net = base & mask
            for h in range(1, min(2 ** (32 - plen) - 1, 1024)):
                t = socket.inet_ntoa(struct.pack('!I', net | h))
                if t != ip:
                    targets.append(t)
        body = json.dumps(self.info()).encode()

        def probe(t):
            try:
                with socket.create_connection((t, PORT), timeout=0.4):
                    pass
            except OSError:
                return
            for proto in ('https', 'http'):
                try:
                    st, data = self._request(t, PORT, proto, 'POST', API + '/register', body, timeout=3)
                    if st == 200:
                        msg = json.loads(data.decode('utf-8', 'replace'))
                        if isinstance(msg, dict):
                            msg.setdefault('port', PORT)
                            msg.setdefault('protocol', proto)
                            self._add_device(msg, t)
                        return
                except Exception:
                    continue

        threads = []
        for t in targets:
            th = threading.Thread(target=probe, args=(t,), daemon=True)
            th.start()
            threads.append(th)
            if len(threads) >= 64:
                for x in threads:
                    x.join()
                threads = []
        for x in threads:
            x.join()

    # ---------- scoperta ----------
    def announce(self):
        data = json.dumps(self.info(True)).encode()
        ips = [ip for ip, _ in self._addrs()] or [None]
        for ip in ips:
            try:
                if ip:
                    self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                self.udp.sendto(data, (GROUP, PORT))
            except OSError:
                pass

    def _announcer(self):
        for delay in (0, 1, 3, 8):     # all'avvio la rete può non essere ancora pronta
            time.sleep(delay)
            self.announce()
        while True:
            time.sleep(30)
            self.announce()
            self._expire()

    def _expire(self):
        now = time.time()
        with self.lock:
            old = [k for k, d in self.devices.items() if now - d['seen'] > 120]
            for k in old:
                del self.devices[k]
        if old:
            self.on_devices(self.list_devices())

    def _listen(self):
        while True:
            try:
                data, (ip, _) = self.udp.recvfrom(65535)
                msg = json.loads(data.decode('utf-8', 'replace'))
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get('fingerprint') == self.fingerprint:
                continue
            self._add_device(msg, ip)
            if msg.get('announce') or msg.get('announcement'):
                threading.Thread(target=self._reply_register, args=(msg, ip), daemon=True).start()

    def _reply_register(self, msg, ip):
        try:
            self._request(ip, int(msg.get('port') or PORT), msg.get('protocol', 'https'),
                          'POST', API + '/register', json.dumps(self.info()).encode(), timeout=5)
        except Exception:
            try:
                self.udp.sendto(json.dumps(self.info(False)).encode(), (GROUP, PORT))
            except OSError:
                pass

    def _add_device(self, msg, ip):
        fp = msg.get('fingerprint') or ip
        if fp == self.fingerprint:
            return
        dev = {'id': fp, 'alias': str(msg.get('alias') or ip)[:60], 'model': msg.get('deviceModel') or '',
               'type': msg.get('deviceType') or 'desktop', 'ip': ip, 'port': int(msg.get('port') or PORT),
               'protocol': msg.get('protocol') or 'https', 'seen': time.time()}
        with self.lock:
            new = fp not in self.devices
            self.devices[fp] = dev
        if new:
            self.on_devices(self.list_devices())

    def list_devices(self):
        with self.lock:
            return sorted([{k: v for k, v in d.items() if k != 'seen'} for d in self.devices.values()],
                          key=lambda d: d['alias'].lower())

    def scan(self):
        self._join()
        self.announce()
        threading.Thread(target=self.http_scan, daemon=True).start()
        return self.list_devices()

    # ---------- ricezione ----------
    def answer(self, rid, accept):
        p = self.pending.get(rid)
        if not p:
            return False
        p['accept'] = bool(accept)
        p['event'].set()
        return True

    def _prepare(self, body, ip):
        if not self.conf.get('receive', True):
            return 403, None
        info, files = body.get('info') or {}, body.get('files') or {}
        if not files:
            return 204, None
        if self.session and time.time() - self.session['last'] < 60:
            return 409, None
        self._add_device(info, ip)
        rid = uuid.uuid4().hex
        ev = threading.Event()
        self.pending[rid] = {'event': ev, 'accept': False}
        total = sum(int(f.get('size') or 0) for f in files.values())
        self.on_request({'id': rid, 'from': str(info.get('alias') or ip)[:60], 'model': info.get('deviceModel') or '',
                         'type': info.get('deviceType') or '', 'count': len(files), 'size': total,
                         'files': [str(f.get('fileName'))[:120] for f in list(files.values())[:6]]})
        ev.wait(90)
        accepted = self.pending.pop(rid, {}).get('accept')
        if not accepted:
            self.on_event({'type': 'rejected'})
            return 403, None
        os.makedirs(DOWNLOADS, exist_ok=True)
        sid = uuid.uuid4().hex
        tokens = {fid: secrets.token_hex(16) for fid in files}
        self.session = {'id': sid, 'ip': ip, 'files': files, 'tokens': tokens, 'done': set(), 'saved': [],
                        'from': str(info.get('alias') or ip), 'last': time.time()}
        return 200, {'sessionId': sid, 'files': tokens}

    def _upload(self, q, ip, rfile, length):
        s = self.session
        sid, fid, tok = q.get('sessionId'), q.get('fileId'), q.get('token')
        if not s or s['id'] != sid or s['ip'] != ip or s['tokens'].get(fid) != tok or fid in s['done']:
            return 403
        meta = s['files'].get(fid) or {}
        name = _free_name(DOWNLOADS, meta.get('fileName') or fid)
        path = os.path.join(DOWNLOADS, name)
        h = hashlib.sha256()
        left = length
        tmp = path + '.risedrop'
        with open(tmp, 'wb') as f:
            while left > 0:
                chunk = rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                left -= len(chunk)
                s['last'] = time.time()
        if left > 0:
            os.remove(tmp)
            return 500
        if meta.get('sha256') and meta['sha256'].lower() != h.hexdigest():
            os.remove(tmp)
            return 422
        os.rename(tmp, path)
        s['done'].add(fid)
        s['saved'].append(path)
        if len(s['done']) == len(s['files']):
            self.on_event({'type': 'received', 'from': s['from'], 'files': s['saved'], 'folder': DOWNLOADS})
            self.session = None
        return 200

    def _cancel(self, q):
        if self.session and self.session['id'] == q.get('sessionId'):
            saved = self.session['saved']
            self.session = None
            self.on_event({'type': 'cancelled', 'files': saved})
        return 200

    # ---------- invio ----------
    def _request(self, ip, port, proto, method, path, body=b'', timeout=10, headers=None):
        if proto == 'http':
            c = http.client.HTTPConnection(ip, port, timeout=timeout)
        else:
            c = http.client.HTTPSConnection(ip, port, timeout=timeout, context=_insecure_ctx())
        h = {'Content-Type': 'application/json'}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def send(self, dev_id, paths, progress=None):
        progress = progress or (lambda p: None)
        with self.lock:
            dev = self.devices.get(dev_id)
        if not dev:
            return {'ok': False, 'message': 'Il dispositivo non è più raggiungibile.'}
        files, local = {}, {}
        for p in paths:
            p = os.path.abspath(p)
            if not os.path.isfile(p):
                continue
            fid = uuid.uuid4().hex
            local[fid] = p
            files[fid] = {'id': fid, 'fileName': os.path.basename(p), 'size': os.path.getsize(p),
                          'fileType': mimetypes.guess_type(p)[0] or 'application/octet-stream'}
        if not files:
            return {'ok': False, 'message': 'Scegli almeno un file (le cartelle non si possono inviare).'}
        progress({'stage': 'waiting', 'device': dev['alias']})
        try:
            st, data = self._request(dev['ip'], dev['port'], dev['protocol'], 'POST', API + '/prepare-upload',
                                     json.dumps({'info': self.info(), 'files': files}).encode(), timeout=100)
        except Exception:
            return {'ok': False, 'message': 'Non riesco a collegarmi a %s.' % dev['alias']}
        if st == 204:
            return {'ok': True, 'count': 0}
        if st == 403:
            return {'ok': False, 'message': '%s ha rifiutato i file.' % dev['alias']}
        if st == 409:
            return {'ok': False, 'message': '%s sta già ricevendo altri file. Riprova tra poco.' % dev['alias']}
        if st != 200:
            return {'ok': False, 'message': 'Invio non riuscito (errore %d).' % st}
        resp = json.loads(data or b'{}')
        sid, tokens = resp.get('sessionId'), resp.get('files') or {}
        total = sum(files[f]['size'] for f in tokens if f in files) or 1
        sent, n = 0, 0
        for fid, tok in tokens.items():
            p = local.get(fid)
            if not p:
                continue
            q = urllib.parse.urlencode({'sessionId': sid, 'fileId': fid, 'token': tok})
            try:
                if dev['protocol'] == 'http':
                    c = http.client.HTTPConnection(dev['ip'], dev['port'], timeout=60)
                else:
                    c = http.client.HTTPSConnection(dev['ip'], dev['port'], timeout=60, context=_insecure_ctx())
                c.putrequest('POST', API + '/upload?' + q)
                c.putheader('Content-Type', 'application/octet-stream')
                c.putheader('Content-Length', str(files[fid]['size']))
                c.endheaders()
                with open(p, 'rb') as f:
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        c.send(chunk)
                        sent += len(chunk)
                        progress({'stage': 'sending', 'device': dev['alias'], 'percent': int(sent * 100 / total)})
                r = c.getresponse()
                r.read()
                c.close()
                if r.status != 200:
                    return {'ok': False, 'message': 'Invio di %s non riuscito.' % os.path.basename(p)}
                n += 1
            except Exception:
                return {'ok': False, 'message': 'Collegamento interrotto durante l’invio.'}
        return {'ok': True, 'count': n, 'device': dev['alias']}


def _handler(drop):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def _reply(self, code, obj=None):
            body = json.dumps(obj).encode() if obj is not None else b''
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n > 4 << 20:
                return None
            try:
                return json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                return None

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == API + '/info':
                return self._reply(200, drop.info())
            self._reply(404)

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            ip = self.client_address[0]
            if u.path == API + '/register':
                b = self._body()
                if isinstance(b, dict):
                    drop._add_device(b, ip)
                return self._reply(200, drop.info())
            if u.path == API + '/prepare-upload':
                b = self._body()
                if not isinstance(b, dict):
                    return self._reply(400)
                code, obj = drop._prepare(b, ip)
                return self._reply(code, obj)
            if u.path == API + '/upload':
                length = int(self.headers.get('Content-Length') or 0)
                return self._reply(drop._upload(q, ip, self.rfile, length))
            if u.path == API + '/cancel':
                return self._reply(drop._cancel(q))
            self._reply(404)
    return H
