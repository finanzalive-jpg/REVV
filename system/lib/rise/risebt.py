"""Rise – inviare file via Bluetooth (OBEX Object Push: funziona con tutti i telefoni, senza app).

Usa bluetoothctl (BlueZ) per accendere, cercare e associare i dispositivi, e il servizio obexd
(pacchetto bluez-obexd) sulla sessione D-Bus per spedire i file. Il telefono chiede di accettare il file.
"""
import os
import re
import select
import subprocess
import threading
import time
import uuid

_jobs = {}
_lock = threading.Lock()


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, LANG='C', LC_ALL='C'))
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, '', str(e)


def _ctl(*args, timeout=20):
    return _run(['bluetoothctl'] + list(args), timeout=timeout)


def _power_on():
    _run(['rfkill', 'unblock', 'bluetooth'])
    _run(['sudo', '-n', 'rfkill', 'unblock', 'bluetooth'])
    _ctl('power', 'on')


def _info(mac):
    rc, out, _ = _ctl('info', mac, timeout=8)
    g = lambda k: (re.search(r'^\s*%s: (.+)$' % k, out, re.M) or [None, ''])[1].strip()
    return {'mac': mac, 'name': g('Alias') or g('Name') or mac, 'icon': g('Icon'), 'paired': g('Paired') == 'yes',
            'connected': g('Connected') == 'yes', 'trusted': g('Trusted') == 'yes'}


def _kind(icon):
    return ('telefono' if 'phone' in icon else 'computer' if 'computer' in icon else
            'cuffie / audio' if 'audio' in icon or 'headset' in icon or 'headphone' in icon else
            'stampante' if 'printer' in icon else 'dispositivo')


def status(_=None):
    rc, out, err = _ctl('show', timeout=8)
    if rc != 0 or 'Controller' not in out:
        return {'ok': False, 'message': 'Questo computer non ha il Bluetooth (o è spento dal tasto o dal BIOS).'}
    return {'ok': True, 'powered': 'Powered: yes' in out, 'name': (re.search(r'Alias: (.+)', out) or [None, ''])[1]}


def devices(a=None):
    """Dispositivi già associati e, se richiesto, quelli vicini (ricerca di qualche secondo)."""
    _power_on()
    secs = int((a or {}).get('scan') or 0)
    if secs:
        _ctl('--timeout', str(min(20, secs)), 'scan', 'on', timeout=secs + 5)
    rc, out, _ = _ctl('devices', timeout=8)
    res = []
    for m in re.finditer(r'^Device ([0-9A-F:]{17}) (.+)$', out, re.M):
        d = _info(m.group(1))
        if re.fullmatch(r'[0-9A-F-]{17}', d['name'].replace(':', '-')):
            continue                                            # senza nome: di solito non è un telefono/PC
        d['kind'] = _kind(d['icon'])
        res.append(d)
    res.sort(key=lambda d: (not d['paired'], d['kind'] != 'telefono', d['name'].lower()))
    return {'ok': True, 'devices': res}


def pair(a):
    """Associa un dispositivo. Sul telefono compare "Associare?" con un codice: va confermato lì."""
    mac = (a.get('mac') or '').upper()
    if not re.fullmatch(r'[0-9A-F:]{17}', mac):
        return {'ok': False, 'message': 'Dispositivo non valido.'}
    _power_on()
    p = subprocess.Popen(['bluetoothctl'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1, env=dict(os.environ, LANG='C'))
    def send(c):
        p.stdin.write(c + '\n')
        p.stdin.flush()
    send('agent KeyboardDisplay')
    send('default-agent')
    send('pair ' + mac)
    ok, msg, t0, buf = False, '', time.time(), ''
    while time.time() - t0 < 40:
        r, _, _ = select.select([p.stdout], [], [], 1)
        if not r:
            continue
        line = p.stdout.readline()
        if not line:
            break
        buf += line
        if re.search(r'Confirm passkey|Request confirmation|Authorize service|\(yes/no\)', line):
            send('yes')                                         # il codice si conferma sul telefono
        if 'Pairing successful' in line or 'AlreadyExists' in line:
            ok = True
            break
        if 'Failed to pair' in line or 'org.bluez.Error' in line:
            msg = line.strip()
            break
    if ok:
        send('trust ' + mac)
        time.sleep(1)
    send('quit')
    try:
        p.wait(3)
    except subprocess.TimeoutExpired:
        p.kill()
    if not ok:
        return {'ok': False, 'message': 'Associazione non riuscita. Sul telefono accetta la richiesta e controlla che il Bluetooth sia visibile. (%s)' % (msg or 'tempo scaduto')}
    return {'ok': True, 'device': _info(mac)}


def _gdbus(*args, timeout=30):
    return _run(['gdbus', 'call', '--session', '--dest', 'org.bluez.obex'] + list(args), timeout=timeout)


def _send_worker(jid, mac, paths):
    j = _jobs[jid]
    sess = None
    try:
        rc, out, err = _gdbus('--object-path', '/org/bluez/obex', '--method', 'org.bluez.obex.Client1.CreateSession',
                              mac, "{'Target': <'opp'>}", timeout=40)
        m = re.search(r"objectpath '([^']+)'", out) or re.search(r"'(/org/bluez/obex/client/session\d+)'", out)
        if rc != 0 or not m:
            raise RuntimeError('il dispositivo non accetta file (%s)' % (err or out).strip()[-160:])
        sess = m.group(1)
        for i, path in enumerate(paths):
            j.update(file=os.path.basename(path), index=i + 1, pct=0, msg='Aspetto che il telefono accetti…')
            rc, out, err = _gdbus('--object-path', sess, '--method', 'org.bluez.obex.ObjectPush1.SendFile', path, timeout=40)
            t = re.search(r"objectpath '([^']+)'", out) or re.search(r"'(/org/bluez/obex/client/session\d+/transfer\d+)'", out)
            if rc != 0 or not t:
                raise RuntimeError((err or out).strip()[-160:] or 'invio rifiutato')
            tr, size, t0 = t.group(1), os.path.getsize(path), time.time()
            while True:
                rc, out, _ = _run(['gdbus', 'call', '--session', '--dest', 'org.bluez.obex', '--object-path', tr, '--method',
                                   'org.freedesktop.DBus.Properties.GetAll', 'org.bluez.obex.Transfer1'], timeout=10)
                st = (re.search(r"'Status': <'(\w+)'>", out) or [None, ''])[1]
                done = int((re.search(r"'Transferred': <uint64 (\d+)>", out) or [None, 0])[1])
                if st == 'active':
                    j.update(msg='Invio…', pct=int(done * 100 / size) if size else 50)
                if st == 'complete' or (rc != 0 and done >= size > 0):
                    break
                if st == 'error' or (rc != 0 and st == ''):
                    if rc != 0 and time.time() - t0 > 3:
                        break                                     # il trasferimento è sparito: finito (o annullato)
                    if st == 'error':
                        raise RuntimeError('il telefono ha rifiutato o interrotto l’invio')
                if time.time() - t0 > 1800:
                    raise RuntimeError('troppo tempo')
                time.sleep(0.7)
        j.update(pct=100, msg='Inviato!', done=True, ok=True)
    except Exception as e:
        j.update(done=True, ok=False, msg='Invio non riuscito: %s' % e)
    finally:
        if sess:
            _gdbus('--object-path', '/org/bluez/obex', '--method', 'org.bluez.obex.Client1.RemoveSession', sess, timeout=10)


def send(a):
    mac = (a.get('mac') or '').upper()
    paths = [os.path.abspath(os.path.expanduser(p)) for p in a.get('paths') or [] if os.path.isfile(os.path.expanduser(p))]
    if not re.fullmatch(r'[0-9A-F:]{17}', mac) or not paths:
        return {'ok': False, 'message': 'Scegli un dispositivo e almeno un file (le cartelle non si possono inviare: comprimile prima in ZIP).'}
    _power_on()
    jid = uuid.uuid4().hex[:8]
    with _lock:
        _jobs[jid] = {'mac': mac, 'files': len(paths), 'pct': 0, 'msg': 'Mi collego…', 'done': False, 'ok': None}
    threading.Thread(target=_send_worker, args=(jid, mac, paths), daemon=True).start()
    return {'ok': True, 'job': jid}


def job(a):
    return {'ok': True, 'job': dict(_jobs.get(a.get('job') or '', {'done': True, 'ok': False, 'msg': 'sconosciuto'}))}


CMDS = {'bt.status': status, 'bt.devices': devices, 'bt.pair': pair, 'bt.send': send, 'bt.job': job}
