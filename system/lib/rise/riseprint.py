"""Rise – stampanti (CUPS): elenco, stampante predefinita, aggiungere per indirizzo, stampare un file.

Le stampanti di rete moderne compaiono da sole (cups-browsed + IPP Everywhere, senza driver); quelle USB
moderne anche (ipp-usb). Per le altre ci sono i driver HP, Brother e Gutenprint.
"""
import os
import re
import shutil
import subprocess
import tempfile


def _run(cmd, timeout=30, sudo=False):
    if sudo and os.geteuid() != 0:
        cmd = ['sudo', '-n'] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, LANG='C', LC_ALL='C'))
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, '', str(e)


def available():
    return bool(shutil.which('lp') and shutil.which('lpstat'))


def printers(_=None):
    if not available():
        return {'ok': False, 'message': 'Il sistema di stampa non è installato in questa versione di Rise.'}
    _, out, _ = _run(['lpstat', '-p', '-d'])
    _, uris, _ = _run(['lpstat', '-v'])
    default = (re.search(r'system default destination: (\S+)', out) or [None, ''])[1]
    uri = dict(re.findall(r'device for (\S+): (\S+)', uris))
    res = []
    for m in re.finditer(r'^printer (\S+) (?:is )?(idle|now printing|disabled)?.*?(?:\.|$)(.*)$', out, re.M):
        name, state = m.group(1), m.group(2) or ''
        u = uri.get(name, '')
        _, info, _ = _run(['lpoptions', '-p', name])
        model = (re.search(r"printer-make-and-model='([^']*)'", info) or re.search(r'printer-make-and-model=(\S+)', info) or [None, ''])[1]
        loc = (re.search(r"printer-location='([^']*)'", info) or [None, ''])[1]
        res.append({'name': name, 'default': name == default, 'state': 'pronta' if state == 'idle' else 'sta stampando' if 'printing' in state else 'spenta o non raggiungibile' if state == 'disabled' else state,
                    'uri': u, 'model': model.replace('\\', ''), 'location': loc,
                    'how': 'USB' if u.startswith(('usb:', 'ipp://localhost')) else 'rete' if u else ''})
    _, jobs, _ = _run(['lpstat', '-o'])
    return {'ok': True, 'printers': res, 'default': default, 'jobs': len([l for l in jobs.splitlines() if l.strip()])}


def discover(_=None):
    """Stampanti trovate in rete o sulla USB che non sono ancora state aggiunte."""
    rc, out, err = _run(['lpinfo', '--include-schemes', 'dnssd,ipp,ipps,usb', '-v'], timeout=40, sudo=True)
    have = {p['uri'] for p in printers().get('printers', [])}
    found = []
    for line in out.splitlines():
        f = line.split(None, 1)
        if len(f) == 2 and f[1] not in have and '://' in f[1] and not f[1].endswith('://'):
            u = f[1]
            name = re.sub(r'%20', ' ', re.sub(r'^\w+://([^/._]+).*$', r'\1', u))
            found.append({'uri': u, 'label': name.replace('%40', '@'), 'how': 'USB' if u.startswith('usb') else 'rete'})
    return {'ok': True, 'found': found}


def add(a):
    """Aggiunge una stampante: da un indirizzo trovato (uri) o dall'IP scritto a mano."""
    uri = (a.get('uri') or '').strip()
    ip = (a.get('ip') or '').strip()
    if not uri and ip:
        if not re.fullmatch(r'[\w.:-]{1,253}', ip):
            return {'ok': False, 'message': 'Indirizzo non valido.'}
        uri = 'ipp://%s/ipp/print' % ip
    if not uri:
        return {'ok': False, 'message': 'Quale stampante?'}
    name = re.sub(r'[^\w-]+', '_', (a.get('name') or re.sub(r'^\w+://', '', uri).split('/')[0].split('.')[0] or 'Stampante'))[:40]
    # prima senza driver (IPP Everywhere: quasi tutte le stampanti dal 2014), poi col modello trovato da CUPS
    for model in ('everywhere', 'driverless'):
        rc, out, err = _run(['lpadmin', '-p', name, '-E', '-v', uri, '-m', model], timeout=90, sudo=True)
        if rc == 0:
            break
    else:
        rc2, out2, _ = _run(['lpinfo', '-m'], timeout=60, sudo=True)
        want = (a.get('model') or name).lower()
        cand = [l.split(None, 1) for l in out2.splitlines() if l.strip()]
        best = next((c[0] for c in cand if len(c) > 1 and all(w in c[1].lower() for w in re.findall(r'\w+', want)[:2])), None)
        if not best:
            return {'ok': False, 'message': 'Non riesco a collegare la stampante: %s' % (err or 'driver non trovato').strip()[-200:]}
        rc, out, err = _run(['lpadmin', '-p', name, '-E', '-v', uri, '-m', best], timeout=90, sudo=True)
        if rc != 0:
            return {'ok': False, 'message': err.strip()[-200:]}
    if a.get('default', True) and not printers().get('default'):
        _run(['lpadmin', '-d', name], sudo=True)
    return {'ok': True, 'name': name}


def set_default(a):
    rc, _, err = _run(['lpadmin', '-d', a.get('name', '')], sudo=True)
    return {'ok': rc == 0, 'message': err.strip()}


def remove(a):
    rc, _, err = _run(['lpadmin', '-x', a.get('name', '')], sudo=True)
    return {'ok': rc == 0, 'message': err.strip()}


def _to_pdf(path):
    """Word, Excel, PowerPoint: si convertono in PDF con LibreOffice prima di stampare."""
    office = shutil.which('libreoffice') or shutil.which('soffice')
    if not office:
        return None
    out = tempfile.mkdtemp(prefix='rise-stampa-')
    _run([office, '--headless', '--convert-to', 'pdf', '--outdir', out, path], timeout=180)
    pdf = os.path.join(out, os.path.splitext(os.path.basename(path))[0] + '.pdf')
    return pdf if os.path.exists(pdf) else None


def print_file(a):
    path = os.path.abspath(os.path.expanduser(a.get('path') or ''))
    if not os.path.isfile(path):
        return {'ok': False, 'message': 'Non trovo il file da stampare.'}
    st = printers()
    if not st.get('ok'):
        return st
    name = a.get('printer') or st.get('default') or (st['printers'][0]['name'] if st['printers'] else '')
    if not name:
        return {'ok': False, 'need': 'printer', 'message': 'Non c’è nessuna stampante: aggiungila in Impostazioni → Stampanti.'}
    src = path
    if re.search(r'\.(docx?|odt|rtf|xlsx?|ods|pptx?|odp|csv)$', path, re.I):
        src = _to_pdf(path)
        if not src:
            return {'ok': False, 'message': 'Per stampare questo tipo di file serve LibreOffice.'}
    cmd = ['lp', '-d', name, '-n', str(max(1, min(99, int(a.get('copies') or 1))))]
    if a.get('bw'):
        cmd += ['-o', 'print-color-mode=monochrome']
    if a.get('duplex'):
        cmd += ['-o', 'sides=two-sided-long-edge']
    if a.get('pages'):
        cmd += ['-P', re.sub(r'[^\d,-]', '', str(a['pages']))]
    rc, out, err = _run(cmd + [src], timeout=60)
    if rc != 0:
        return {'ok': False, 'message': 'La stampa non è partita: %s' % (err or out).strip()[-200:]}
    return {'ok': True, 'printer': name, 'job': (re.search(r'request id is (\S+)', out) or [None, ''])[1]}


def test_page(a):
    name = a.get('name') or printers().get('default')
    for f in ('/usr/share/cups/data/testprint', '/usr/share/cups/data/default-testpage.pdf'):
        if os.path.exists(f):
            rc, out, err = _run(['lp', '-d', name, f])
            return {'ok': rc == 0, 'message': err.strip()}
    return {'ok': False, 'message': 'Pagina di prova non disponibile.'}


def cancel_all(_=None):
    rc, _, err = _run(['cancel', '-a'])
    return {'ok': rc == 0}


CMDS = {'print.list': printers, 'print.discover': discover, 'print.add': add, 'print.default': set_default,
        'print.remove': remove, 'print.file': print_file, 'print.test': test_page, 'print.cancel': cancel_all}
