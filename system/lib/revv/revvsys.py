"""Revv – funzioni di sistema richiamate dal desktop (rete, audio, file, hardware…).
Ogni funzione riceve un dizionario di argomenti e restituisce dati serializzabili in JSON."""
import base64, json, os, pwd, re, subprocess, threading, time

HOME = os.path.expanduser('~')
USER = pwd.getpwuid(os.getuid()).pw_name


def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:  # comando mancante o scaduto
        return 1, '', str(e)


def root(cmd, timeout=20):
    """Esegue come amministratore (sudo senza password sulla chiavetta live)."""
    rc, out, err = run(cmd, timeout)
    if rc != 0:
        rc, out, err = run(['sudo', '-n'] + cmd, timeout)
    return rc, out, err


def nm_fields(line):
    # nmcli -t separa i campi con ":" e scrive i ":" interni come "\:"
    parts = re.split(r'(?<!\\):', line)
    return [p.replace('\\:', ':').replace('\\\\', '\\') for p in parts]


# ---------------- Rete ----------------
def net_status(_):
    rc, out, _ = run(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE,CONNECTION', 'device'])
    devs = []
    for l in out.splitlines():
        f = nm_fields(l)
        if len(f) >= 4:
            devs.append({'device': f[0], 'type': f[1], 'state': f[2], 'connection': f[3]})
    wifi = [d for d in devs if d['type'] == 'wifi']
    eth = [d for d in devs if d['type'] == 'ethernet']
    _, radio, _ = run(['nmcli', 'radio', 'wifi'])
    active = next((d for d in devs if d['type'] in ('wifi', 'ethernet') and d['state'].startswith('connected')), None)
    conn = 'none'
    if active:
        _, conn, _ = run(['nmcli', 'networking', 'connectivity', 'check'], timeout=12)
    return {'nm': rc == 0, 'wifiDevice': bool(wifi), 'wifiName': wifi[0]['device'] if wifi else '', 'wifiOn': radio.strip() == 'enabled',
            'ethernet': bool(eth), 'ethernetCable': any(d['state'] != 'unavailable' for d in eth),
            'active': active, 'connectivity': conn or 'unknown'}


def net_scan(_):
    rc, out, err = run(['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list', '--rescan', 'yes'], timeout=30)
    nets = {}
    for l in out.splitlines():
        f = nm_fields(l)
        if len(f) < 4 or not f[1]:
            continue
        ssid = f[1]
        sig = int(f[2]) if f[2].isdigit() else 0
        sec = '' if f[3] in ('', '--') else f[3]
        cur = nets.get(ssid)
        if not cur or sig > cur['signal'] or f[0] == '*':
            nets[ssid] = {'ssid': ssid, 'signal': sig, 'secure': bool(sec), 'security': sec,
                          'inUse': f[0] == '*' or (cur or {}).get('inUse', False)}
    return sorted(nets.values(), key=lambda n: (not n['inUse'], -n['signal']))


def net_connect(a):
    ssid, pw = a.get('ssid', ''), a.get('password', '')
    cmd = ['nmcli', 'device', 'wifi', 'connect', ssid] + (['password', pw] if pw else [])
    rc, out, err = run(cmd, timeout=60)
    if rc == 0:
        return {'ok': True}
    msg = err or out
    if 'Secrets were required' in msg or 'password' in msg.lower() or '802-1x' in msg:
        msg = 'La password non è corretta.'
    elif 'No network with SSID' in msg:
        msg = 'La rete non è più raggiungibile. Avvicinati al router e riprova.'
    return {'ok': False, 'message': msg}


def net_radio(a):
    rc, out, err = run(['nmcli', 'radio', 'wifi', 'on' if a.get('on') else 'off'])
    return {'ok': rc == 0, 'message': err}


def net_diag(_):
    steps = []
    st = net_status(None)
    # 1. scheda di rete
    if not st['nm']:
        root(['systemctl', 'restart', 'NetworkManager'])
        time.sleep(3)
        st = net_status(None)
    has = st['wifiDevice'] or st['ethernet']
    steps.append({'name': 'Scheda di rete del computer', 'ok': has,
                  'detail': ('Wi‑Fi' if st['wifiDevice'] else '') + (' e cavo' if st['wifiDevice'] and st['ethernet'] else ('cavo' if st['ethernet'] else '')) if has else 'non trovata: probabilmente manca il driver'})
    if not has:
        return {'steps': steps, 'ok': False, 'summary': 'Non trovo nessuna scheda di rete. Apri Sistema e hardware per vedere il dettaglio.'}
    # 2. collegamento
    fixed = ''
    if st['wifiDevice'] and not st['wifiOn'] and not st['active']:
        run(['nmcli', 'radio', 'wifi', 'on'])
        time.sleep(4)
        fixed = 'il Wi‑Fi era spento, l’ho acceso'
        st = net_status(None)
    if not st['active'] and st['wifiDevice']:
        run(['nmcli', 'device', 'connect', st['wifiName']], timeout=20)
        st = net_status(None)
    act = st['active']
    steps.append({'name': 'Collegamento alla rete', 'ok': bool(act),
                  'detail': (fixed + ' · ' if fixed else '') + (('Wi‑Fi ' + act['connection']) if act and act['type'] == 'wifi' else ('cavo di rete' if act else 'non collegato: scegli una rete qui sotto'))})
    if not act:
        return {'steps': steps, 'ok': False, 'summary': 'Il computer non è collegato a nessuna rete. Scegli la tua rete Wi‑Fi qui sotto o collega il cavo.'}
    # 3. router
    _, route, _ = run(['ip', 'route', 'show', 'default'])
    m = re.search(r'default via (\S+)', route)
    gw = m.group(1) if m else ''
    rc = run(['ping', '-c', '1', '-W', '2', gw])[0] if gw else 1
    steps.append({'name': 'Router', 'ok': rc == 0, 'detail': ('risponde (' + gw + ')') if rc == 0 else 'non risponde'})
    if rc != 0:
        root(['nmcli', 'device', 'reapply', act['device']])
        return {'steps': steps, 'ok': False, 'summary': 'Il router non risponde. Prova a spegnerlo e riaccenderlo, poi controlla di nuovo.'}
    # 4. internet
    rc = run(['ping', '-c', '1', '-W', '3', '1.1.1.1'])[0]
    if rc != 0 and st.get('connectivity') == 'full':
        rc = 0  # alcune reti bloccano il ping ma internet funziona
    steps.append({'name': 'Uscita verso internet', 'ok': rc == 0, 'detail': 'ok' if rc == 0 else 'bloccata'})
    if rc != 0:
        return {'steps': steps, 'ok': False, 'summary': 'Il router c’è ma internet no. Di solito è un problema della linea: controlla le luci del router o chiama il gestore.'}
    # 5. dns
    t0 = time.time()
    rc = run(['getent', 'hosts', 'ubuntu.com'], timeout=8)[0]
    ms = int((time.time() - t0) * 1000)
    steps.append({'name': 'Traduzione dei nomi dei siti (DNS)', 'ok': rc == 0, 'detail': f'ok · {ms} ms' if rc == 0 else 'non funziona'})
    if rc != 0:
        return {'steps': steps, 'ok': False, 'summary': 'Internet funziona ma i nomi dei siti non vengono trovati (DNS). Riavvia il router; se non basta, scrivimi.'}
    return {'steps': steps, 'ok': True, 'summary': 'Tutto a posto: internet funziona.' + (' (' + fixed + ')' if fixed else '')}


# ---------------- Audio e luminosità ----------------
def audio_get(_):
    rc, out, _ = run(['wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@'])
    if rc != 0:
        return {'available': False}
    m = re.search(r'([\d.]+)', out)
    vol = round(float(m.group(1)) * 100) if m else 0
    _, info, _ = run(['wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@'])
    d = re.search(r'node\.description = "([^"]+)"', info)
    return {'available': True, 'volume': vol, 'muted': 'MUTED' in out, 'name': d.group(1) if d else ''}


def audio_set(a):
    v = max(0, min(100, int(a.get('volume', 50))))
    run(['wpctl', 'set-volume', '-l', '1.0', '@DEFAULT_AUDIO_SINK@', f'{v / 100:.2f}'])
    run(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '1' if v == 0 else '0'])
    return {'ok': True}


def bright_get(_):
    rc, out, _ = run(['brightnessctl', '-m', '-c', 'backlight'])
    if rc != 0 or not out:
        return {'available': False}
    f = out.split(',')
    return {'available': True, 'percent': int(f[3].rstrip('%')) if len(f) > 3 else 100}


def bright_set(a):
    p = max(5, min(100, int(a.get('percent', 100))))
    rc = root(['brightnessctl', '-q', '-c', 'backlight', 'set', f'{p}%'])[0]
    return {'ok': rc == 0}


def power(a):
    act = 'reboot' if a.get('action') == 'reboot' else 'poweroff'
    run(['sync'])
    rc, _, err = root(['systemctl', act])
    return {'ok': rc == 0, 'message': err}


# ---------------- Sistema e hardware ----------------
def sys_info(_):
    mem = {}
    for l in open('/proc/meminfo'):
        k, v = l.split(':')
        mem[k] = int(v.split()[0]) * 1024
    cpu = ''
    for l in open('/proc/cpuinfo'):
        if l.startswith('model name'):
            cpu = l.split(':', 1)[1].strip()
            break
    cores = os.cpu_count()
    _, boot, _ = run(['systemd-analyze'])
    m = re.search(r'graphical\.target reached after ([\d.]+)(ms|s|min)', boot) or re.search(r'=\s*([\d.]+)(ms|s|min)', boot)
    boot_s = None
    if m:
        boot_s = float(m.group(1)) / (1000 if m.group(2) == 'ms' else 1) * (60 if m.group(2) == 'min' else 1)
    hw = []
    _, out, _ = run(['lspci', '-vmmk'])
    for rec in out.split('\n\n'):
        r = {}
        for l in rec.splitlines():
            if ':' in l:
                k, v = l.split(':', 1)
                r[k.strip()] = v.strip()
        cls = r.get('Class', '')
        kind = None
        if cls in ('VGA compatible controller', '3D controller', 'Display controller'):
            kind = 'video'
        elif cls == 'Network controller':
            kind = 'wifi'
        elif cls == 'Ethernet controller':
            kind = 'ethernet'
        elif cls in ('Audio device', 'Multimedia audio controller'):
            kind = 'audio'
        if kind:
            hw.append({'kind': kind, 'name': (r.get('Vendor', '') + ' ' + r.get('Device', '')).strip(),
                       'driver': r.get('Driver', '')})
    _, usb, _ = run(['lsusb'])
    for l in usb.splitlines():
        name = l.split(' ', 6)[-1] if l.count(' ') >= 6 else l
        low = name.lower()
        kind = 'webcam' if ('camera' in low or 'webcam' in low) else 'printer' if 'printer' in low or 'deskjet' in low or 'laserjet' in low else 'bluetooth' if 'bluetooth' in low else None
        if kind:
            hw.append({'kind': kind, 'name': name, 'driver': 'usb'})
    bat = None
    for b in ('BAT0', 'BAT1'):
        p = f'/sys/class/power_supply/{b}/capacity'
        if os.path.exists(p):
            st = open(f'/sys/class/power_supply/{b}/status').read().strip()
            bat = {'percent': int(open(p).read()), 'status': st}
            break
    st = os.statvfs('/')
    return {'memTotal': mem.get('MemTotal', 0), 'memUsed': mem.get('MemTotal', 0) - mem.get('MemAvailable', 0),
            'cpu': cpu, 'cores': cores, 'kernel': os.uname().release, 'boot': boot_s, 'hardware': hw,
            'battery': bat, 'live': os.path.exists('/run/casper') or os.path.exists('/cdrom/casper'),
            'diskFree': st.f_bavail * st.f_frsize}


# ---------------- File e dischi ----------------
def fs_roots(_):
    rc, out, _ = run(['lsblk', '-J', '-b', '-o', 'NAME,PATH,LABEL,SIZE,FSTYPE,MOUNTPOINTS,TYPE,RM'])
    drives = []
    try:
        def walk(nodes):
            for n in nodes:
                if n.get('type') == 'part' or (n.get('type') == 'disk' and n.get('fstype')):
                    fs = n.get('fstype') or ''
                    mps = [m for m in (n.get('mountpoints') or []) if m]
                    live = any(m.startswith(('/cdrom', '/rofs', '/run/live', '/isodevice')) for m in mps)
                    if fs in ('ntfs', 'vfat', 'exfat', 'ext4', 'btrfs', 'xfs') and not live and int(n.get('size') or 0) > 200 * 1024 * 1024:
                        drives.append({'dev': n['path'], 'label': n.get('label') or '', 'size': int(n.get('size') or 0),
                                       'fs': fs, 'removable': bool(n.get('rm')), 'mount': mps[0] if mps else ''})
                walk(n.get('children') or [])
        walk(json.loads(out).get('blockdevices', []))
    except Exception:
        pass
    return {'home': HOME, 'drives': drives}


def fs_mount(a):
    dev = a.get('dev', '')
    if not re.match(r'^/dev/[\w-]+$', dev):
        return {'ok': False, 'message': 'disco non valido'}
    rc, out, _ = run(['lsblk', '-no', 'MOUNTPOINTS,FSTYPE,LABEL', dev])
    first = out.splitlines()[0] if out else ''
    for m in first.split():
        if m.startswith('/'):
            return {'ok': True, 'mount': m}
    _, fs, _ = run(['lsblk', '-no', 'FSTYPE', dev])
    _, label, _ = run(['lsblk', '-no', 'LABEL', dev])
    name = re.sub(r'[^\w.-]', '_', label.strip() or os.path.basename(dev))
    mp = f'/media/{USER}/{name}'
    root(['mkdir', '-p', mp])
    uid, gid = os.getuid(), os.getgid()
    fs = fs.strip()
    if fs == 'ntfs':
        rc, _, err = root(['mount', '-t', 'ntfs3', '-o', f'ro,uid={uid},gid={gid}', dev, mp])
    elif fs in ('vfat', 'exfat'):
        rc, _, err = root(['mount', '-o', f'ro,uid={uid},gid={gid}', dev, mp])
    else:
        rc, _, err = root(['mount', '-o', 'ro', dev, mp])
    if rc != 0:
        msg = err
        if 'hibernat' in err.lower() or 'dirty' in err.lower() or 'unclean' in err.lower():
            msg = 'Windows non è stato spento del tutto (avvio rapido attivo). Da Windows tieni premuto Maiusc mentre premi Arresta, poi riprova.'
        return {'ok': False, 'message': msg}
    return {'ok': True, 'mount': mp}


def fs_list(a):
    path = os.path.abspath(os.path.expanduser(a.get('path') or HOME))
    dirs, files = [], []
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith('.') or e.name in ('$RECYCLE.BIN', 'System Volume Information', 'lost+found'):
                    continue
                try:
                    st = e.stat()
                    if e.is_dir():
                        dirs.append({'name': e.name, 'mtime': int(st.st_mtime * 1000)})
                    else:
                        files.append({'name': e.name, 'size': st.st_size, 'mtime': int(st.st_mtime * 1000)})
                except OSError:
                    pass
                if len(dirs) + len(files) > 5000:
                    break
    except PermissionError:
        return {'ok': False, 'message': 'Non hai il permesso di aprire questa cartella.'}
    except FileNotFoundError:
        return {'ok': False, 'message': 'La cartella non esiste più.'}
    return {'ok': True, 'path': path, 'dirs': dirs, 'files': files}


def fs_read(a):
    path = os.path.abspath(a.get('path', ''))
    size = os.path.getsize(path)
    if size > 80 * 1024 * 1024:
        return {'ok': False, 'message': 'File troppo grande per l’anteprima (oltre 80 MB).'}
    with open(path, 'rb') as f:
        return {'ok': True, 'data': base64.b64encode(f.read()).decode()}



def sys_boot(_):
    """Secondi dall'avvio del kernel al desktop (systemd-analyze), quando l'avvio è finito."""
    rc, out, _ = run(['systemd-analyze'], timeout=8)
    if rc != 0 or 'graphical.target' not in out and '=' not in out:
        return {'seconds': None}
    tot = 0.0
    m = re.search(r'Startup finished in (.*?)(?:\n|$)', out)
    if not m:
        return {'seconds': None}
    for val, unit in re.findall(r'([\d.]+)(ms|min|s)\b', m.group(1).split('=')[0]):
        if 'firmware' in m.group(1) or 'loader' in m.group(1):
            pass
        tot += float(val) / 1000 if unit == 'ms' else float(val) * (60 if unit == 'min' else 1)
    # escludi firmware e boot loader: contano solo kernel, avvio iniziale e sistema
    for part in re.findall(r'([\d.]+)(ms|min|s) \((firmware|loader)\)', m.group(1)):
        v = float(part[0]) / 1000 if part[1] == 'ms' else float(part[0]) * (60 if part[1] == 'min' else 1)
        tot -= v
    return {'seconds': round(tot, 1) if tot > 0 else None}


# ---------------- Gestione file ----------------
def _safe(p):
    p = os.path.abspath(os.path.expanduser(p or ''))
    if not p or p == '/':
        raise ValueError('percorso non valido')
    return p


def _free_name(dest_dir, name):
    base, ext = os.path.splitext(name)
    cand, n = name, 2
    while os.path.exists(os.path.join(dest_dir, cand)):
        cand = f'{base} ({n}){ext}'
        n += 1
    return cand


def fs_mkdir(a):
    d = _safe(a.get('path'))
    name = (a.get('name') or 'Nuova cartella').strip().replace('/', '-')
    name = _free_name(d, name)
    os.makedirs(os.path.join(d, name))
    return {'ok': True, 'name': name}


def fs_rename(a):
    src = _safe(a.get('path'))
    new = (a.get('name') or '').strip().replace('/', '-')
    if not new:
        return {'ok': False, 'message': 'Scrivi un nome.'}
    dst = os.path.join(os.path.dirname(src), new)
    if os.path.exists(dst):
        return {'ok': False, 'message': 'Esiste già un elemento con questo nome.'}
    os.rename(src, dst)
    return {'ok': True}


def _transfer(a, move):
    import shutil
    dest = _safe(a.get('dest'))
    done = 0
    for src in a.get('paths') or []:
        src = _safe(src)
        if os.path.dirname(src) == dest and move:
            continue
        if dest == src or dest.startswith(src + os.sep):
            return {'ok': False, 'message': 'Non puoi copiare una cartella dentro sé stessa.'}
        name = _free_name(dest, os.path.basename(src))
        target = os.path.join(dest, name)
        if move:
            shutil.move(src, target)
        elif os.path.isdir(src):
            shutil.copytree(src, target, symlinks=True)
        else:
            shutil.copy2(src, target)
        done += 1
    return {'ok': True, 'count': done}


def fs_copy(a):
    return _transfer(a, False)


def fs_move(a):
    return _transfer(a, True)


def fs_trash(a):
    bad = []
    for p in a.get('paths') or []:
        rc, _, err = run(['gio', 'trash', '--', _safe(p)])
        if rc != 0:
            bad.append(os.path.basename(p))
    return {'ok': not bad, 'message': ('Non riesco a spostare nel cestino: ' + ', '.join(bad)) if bad else ''}


TRASH = os.path.join(HOME, '.local/share/Trash')


def fs_trash_list(_):
    files = []
    fdir, idir = os.path.join(TRASH, 'files'), os.path.join(TRASH, 'info')
    if os.path.isdir(fdir):
        for n in os.listdir(fdir):
            p = os.path.join(fdir, n)
            orig, when = '', 0
            try:
                for l in open(os.path.join(idir, n + '.trashinfo')):
                    if l.startswith('Path='):
                        from urllib.parse import unquote
                        orig = unquote(l[5:].strip())
                    if l.startswith('DeletionDate='):
                        when = int(time.mktime(time.strptime(l[13:].strip(), '%Y-%m-%dT%H:%M:%S')) * 1000)
            except Exception:
                pass
            st = os.lstat(p)
            files.append({'name': n, 'dir': os.path.isdir(p), 'size': st.st_size, 'mtime': when or int(st.st_mtime * 1000), 'orig': orig})
    return {'ok': True, 'files': files}


def fs_trash_restore(a):
    import shutil
    out = []
    for n in a.get('names') or []:
        n = os.path.basename(n)
        info = os.path.join(TRASH, 'info', n + '.trashinfo')
        orig = ''
        try:
            from urllib.parse import unquote
            for l in open(info):
                if l.startswith('Path='):
                    orig = unquote(l[5:].strip())
        except Exception:
            pass
        if not orig:
            continue
        os.makedirs(os.path.dirname(orig), exist_ok=True)
        dst = os.path.join(os.path.dirname(orig), _free_name(os.path.dirname(orig), os.path.basename(orig)))
        shutil.move(os.path.join(TRASH, 'files', n), dst)
        try:
            os.remove(info)
        except OSError:
            pass
        out.append(dst)
    return {'ok': True, 'restored': out}


def fs_trash_empty(_):
    rc, _, err = run(['gio', 'trash', '--empty'])
    return {'ok': rc == 0, 'message': err}


def fs_search(a):
    q = (a.get('q') or '').strip()
    if len(q) < 2:
        return {'ok': True, 'results': []}
    roots = [HOME, '/media', '/mnt']
    res = []
    rc, out, _ = run(['plocate', '-i', '-l', '400', '--', q], timeout=10)
    if rc == 0 or out:
        for p in out.splitlines():
            if not any(p == r or p.startswith(r + '/') for r in roots):
                continue
            rel = p[len(HOME):] if p.startswith(HOME) else p
            if '/.' in rel or q.lower() not in os.path.basename(p).lower():
                continue
            res.append(p)
            if len(res) >= 40:
                break
    if not res:  # indice non ancora pronto: cerca direttamente nella cartella personale
        for dp, dns, fns in os.walk(HOME):
            dns[:] = [d for d in dns if not d.startswith('.')]
            for n in dns + fns:
                if q.lower() in n.lower():
                    res.append(os.path.join(dp, n))
            if len(res) >= 40 or dp.count('/') - HOME.count('/') > 6:
                break
    items = []
    for p in res[:40]:
        try:
            st = os.stat(p)
            items.append({'path': p, 'name': os.path.basename(p), 'dir': os.path.isdir(p), 'size': st.st_size, 'mtime': int(st.st_mtime * 1000)})
        except OSError:
            pass
    return {'ok': True, 'results': items}


def index_update(_):
    rc, _, err = root(['nice', '-n', '19', 'ionice', '-c3', 'updatedb'], timeout=600)
    return {'ok': rc == 0}


# ---------------- Rete: PC vicini e cartelle condivise ----------------
def net_hosts(_):
    hosts = {}
    rc, out, _ = run(['avahi-browse', '-t', '-r', '-p', '_smb._tcp'], timeout=8)
    for l in out.splitlines():
        f = l.split(';')
        if len(f) > 7 and f[0] == '=' and f[2] in ('IPv4', 'IPv6'):
            name = f[3].replace('\\032', ' ')
            if f[2] == 'IPv4' or name not in hosts:
                hosts[name] = {'name': name, 'host': f[6].rstrip('.'), 'address': f[7]}
    rc, out, _ = run(['nmblookup', '-S', '*'], timeout=6)
    cur = None
    for l in out.splitlines():
        m = re.match(r'^(\d+\.\d+\.\d+\.\d+) \*<00>', l)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r'^\s+(\S+)\s+<00> -\s+(?!<GROUP>)', l)
        if m and cur and not any(h['address'] == cur for h in hosts.values()):
            hosts[m.group(1)] = {'name': m.group(1), 'host': m.group(1), 'address': cur}
            cur = None
    me = os.uname().nodename.lower()
    return sorted([h for h in hosts.values() if h['name'].lower() != me], key=lambda h: h['name'].lower())


def _smb_auth(a):
    u, p = a.get('user') or '', a.get('password') or ''
    return ['-U', f'{u}%{p}'] if u else ['-N']


def net_shares(a):
    host = (a.get('address') or a.get('host') or '').strip().strip('\\/')
    if not re.match(r'^[\w.\-:]+$', host):
        return {'ok': False, 'message': 'Indirizzo non valido.'}
    rc, out, err = run(['smbclient', '-g', '-L', '//' + host] + _smb_auth(a), timeout=15)
    shares = []
    for l in out.splitlines():
        f = l.split('|')
        if len(f) >= 2 and f[0] == 'Disk' and not f[1].endswith('$'):
            shares.append({'name': f[1], 'comment': f[2] if len(f) > 2 else ''})
    if rc != 0 and not shares:
        msg = err or out
        if 'LOGON_FAILURE' in msg or 'ACCESS_DENIED' in msg:
            return {'ok': False, 'auth': True, 'message': 'Serve nome utente e password di quel computer.'}
        return {'ok': False, 'message': 'Non riesco a raggiungere questo computer. Controlla che sia acceso e sulla stessa rete.'}
    return {'ok': True, 'shares': shares}


def net_mount(a):
    host = (a.get('address') or a.get('host') or '').strip().strip('\\/')
    share = (a.get('share') or '').strip('/')
    if not re.match(r'^[\w.\-:]+$', host) or not share or '/' in share:
        return {'ok': False, 'message': 'Indirizzo non valido.'}
    mp = f'/media/{USER}/rete/{re.sub(r"[^\w.-]", "_", host)}-{re.sub(r"[^\w.-]", "_", share)}'
    if os.path.ismount(mp):
        return {'ok': True, 'mount': mp}
    root(['mkdir', '-p', mp])
    uid, gid = os.getuid(), os.getgid()
    base = f'uid={uid},gid={gid},file_mode=0664,dir_mode=0775,iocharset=utf8'
    cred = f",username={a['user']},password={a.get('password', '')}" if a.get('user') else ',guest'
    err = ''
    for vers in ('3.1.1', '3.0', '2.1', '1.0'):
        rc, _, err = root(['mount', '-t', 'cifs', f'//{host}/{share}', mp, '-o', base + cred + f',vers={vers}'], timeout=20)
        if rc == 0:
            return {'ok': True, 'mount': mp}
    if 'Permission denied' in err or 'error(13)' in err:
        return {'ok': False, 'auth': True, 'message': 'Nome utente o password non corretti.'}
    return {'ok': False, 'message': 'Non riesco ad aprire la cartella condivisa.'}


SHARES_CONF = '/etc/samba/revv-shares.conf'


def _shares_read():
    out, cur = [], None
    try:
        for l in open(SHARES_CONF):
            l = l.strip()
            m = re.match(r'^\[(.+)\]$', l)
            if m:
                cur = {'name': m.group(1), 'path': '', 'writable': False}
                out.append(cur)
            elif cur and '=' in l:
                k, v = [x.strip() for x in l.split('=', 1)]
                if k == 'path':
                    cur['path'] = v
                if k == 'read only':
                    cur['writable'] = v.lower() == 'no'
    except FileNotFoundError:
        pass
    return out


def _shares_write(lst):
    txt = '# Cartelle condivise da Revv (generato automaticamente)\n'
    for s in lst:
        txt += (f"\n[{s['name']}]\n   path = {s['path']}\n   read only = {'no' if s['writable'] else 'yes'}\n"
                f"   guest ok = yes\n   browseable = yes\n   force user = {USER}\n")
    p = subprocess.run(['sudo', '-n', 'tee', SHARES_CONF], input=txt, text=True, capture_output=True)
    root(['smbcontrol', 'smbd', 'reload-config'])
    return p.returncode == 0


def share_list(_):
    return {'ok': True, 'shares': _shares_read(), 'host': os.uname().nodename}


def share_add(a):
    path = _safe(a.get('path'))
    lst = [s for s in _shares_read() if s['path'] != path]
    name = re.sub(r'[^\w\- ]', '', os.path.basename(path))[:40] or 'Cartella'
    names = {s['name'].lower() for s in lst}
    base, n = name, 2
    while name.lower() in names:
        name = f'{base} {n}'
        n += 1
    lst.append({'name': name, 'path': path, 'writable': bool(a.get('writable'))})
    ok = _shares_write(lst)
    return {'ok': ok, 'name': name, 'host': os.uname().nodename}


def share_remove(a):
    path = _safe(a.get('path'))
    ok = _shares_write([s for s in _shares_read() if s['path'] != path])
    return {'ok': ok}

CMDS = {'net.status': net_status, 'net.scan': net_scan, 'net.connect': net_connect, 'net.radio': net_radio,
        'net.diag': net_diag, 'audio.get': audio_get, 'audio.set': audio_set, 'bright.get': bright_get,
        'bright.set': bright_set, 'power': power, 'sys.info': sys_info, 'fs.roots': fs_roots,
        'fs.mount': fs_mount, 'fs.list': fs_list, 'fs.read': fs_read,
        'fs.mkdir': fs_mkdir, 'fs.rename': fs_rename, 'fs.copy': fs_copy, 'fs.move': fs_move, 'fs.trash': fs_trash,
        'fs.trashList': fs_trash_list, 'fs.trashRestore': fs_trash_restore, 'fs.trashEmpty': fs_trash_empty,
        'fs.search': fs_search, 'sys.boot': sys_boot, 'index.update': index_update,
        'net.hosts': net_hosts, 'net.shares': net_shares, 'net.mount': net_mount,
        'share.list': share_list, 'share.add': share_add, 'share.remove': share_remove}

