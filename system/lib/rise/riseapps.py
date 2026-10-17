"""Rise – Negozio: installa programmi da Flathub, il catalogo libero di app per Linux (oltre 3000 app).

Le app Flathub girano "in una scatola" (Flatpak): non possono rompere il sistema e si tolgono senza
lasciare tracce. Il catalogo e le icone arrivano da flathub.org; l'installazione la fa il comando flatpak.
"""
import json
import os
import re
import subprocess
import threading
import urllib.request

ICON = 'https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/%s.png'
APPS_FILE = os.path.expanduser('~/.config/rise/apps.json')

# Le app che la gente cerca di più, divise per tipo (id Flathub, nome, descrizione breve)
CATALOG = [
    ('Internet', [
        ('com.google.Chrome', 'Google Chrome', 'Il browser di Google'),
        ('org.mozilla.firefox', 'Firefox', 'Browser libero e veloce'),
        ('com.brave.Browser', 'Brave', 'Browser che blocca la pubblicità'),
        ('org.mozilla.Thunderbird', 'Thunderbird', 'Posta elettronica'),
        ('org.telegram.desktop', 'Telegram', 'Messaggi e chiamate'),
        ('com.rtosta.zapzap', 'ZapZap', 'WhatsApp sul computer'),
        ('com.discordapp.Discord', 'Discord', 'Chat vocale e di gruppo'),
        ('us.zoom.Zoom', 'Zoom', 'Videochiamate e riunioni'),
        ('com.anydesk.Anydesk', 'AnyDesk', 'Assistenza a distanza'),
        ('org.qbittorrent.qBittorrent', 'qBittorrent', 'Scarica file torrent'),
    ]),
    ('Ufficio', [
        ('org.libreoffice.LibreOffice', 'LibreOffice', 'Word, Excel e PowerPoint liberi'),
        ('org.onlyoffice.desktopeditors', 'ONLYOFFICE', 'Ufficio compatibile con Microsoft Office'),
        ('com.github.xournalpp.xournalpp', 'Xournal++', 'Firma e scrivi sui PDF'),
        ('org.gnome.Evince', 'Lettore PDF', 'Apri e stampa i PDF'),
        ('md.obsidian.Obsidian', 'Obsidian', 'Appunti e idee collegate'),
        ('org.gnome.Calendar', 'Calendario', 'Appuntamenti e promemoria'),
    ]),
    ('Musica e video', [
        ('com.spotify.Client', 'Spotify', 'Musica e podcast'),
        ('org.videolan.VLC', 'VLC', 'Guarda qualsiasi video'),
        ('com.obsproject.Studio', 'OBS Studio', 'Registra lo schermo e fai dirette'),
        ('org.kde.kdenlive', 'Kdenlive', 'Monta i tuoi video'),
        ('org.audacityteam.Audacity', 'Audacity', 'Registra e modifica audio'),
        ('com.stremio.Stremio', 'Stremio', 'Film e serie in streaming'),
    ]),
    ('Grafica e foto', [
        ('org.gimp.GIMP', 'GIMP', 'Ritocco foto (come Photoshop)'),
        ('org.inkscape.Inkscape', 'Inkscape', 'Disegno vettoriale e loghi'),
        ('org.kde.krita', 'Krita', 'Pittura digitale'),
        ('org.darktable.Darktable', 'darktable', 'Sviluppo foto RAW'),
        ('org.blender.Blender', 'Blender', 'Grafica e animazione 3D'),
    ]),
    ('Giochi', [
        ('com.valvesoftware.Steam', 'Steam', 'Il negozio di videogiochi'),
        ('org.libretro.RetroArch', 'RetroArch', 'Giochi delle vecchie console'),
        ('org.gnome.Chess', 'Scacchi', 'Gioca a scacchi'),
        ('org.kde.kpat', 'Solitario', 'Solitari di carte'),
        ('net.minetest.Minetest', 'Luanti', 'Costruisci mondi a blocchi'),
    ]),
    ('Utilità', [
        ('org.gnome.Calculator', 'Calcolatrice scientifica', 'Calcoli avanzati'),
        ('org.kde.kdeconnect', 'KDE Connect', 'Collega il telefono al computer'),
        ('com.github.tchx84.Flatseal', 'Flatseal', 'Permessi delle app'),
        ('io.github.peazip.PeaZip', 'PeaZip', 'Apri file zip e rar'),
        ('org.keepassxc.KeePassXC', 'KeePassXC', 'Tieni al sicuro le password'),
    ]),
]

_jobs = {}            # id -> {'running', 'pct', 'msg', 'error'}
_lock = threading.Lock()


def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, '', str(e)


def _flatpak():
    return os.path.exists('/usr/bin/flatpak')


def installed_ids():
    if not _flatpak():
        return {}
    rc, out, _ = _run(['flatpak', 'list', '--app', '--columns=application,name'])
    res = {}
    for l in out.splitlines():
        p = l.split('\t')
        if p and p[0]:
            res[p[0]] = p[1] if len(p) > 1 else p[0]
    return res


def catalog(_=None):
    inst = installed_ids()
    cats = [{'name': c, 'apps': [{'id': i, 'name': n, 'desc': d, 'icon': ICON % i, 'installed': i in inst}
                                 for i, n, d in apps]} for c, apps in CATALOG]
    return {'ok': True, 'available': _flatpak(), 'categories': cats,
            'jobs': {k: dict(v) for k, v in _jobs.items()}}


def search(a):
    q = (a.get('q') or '').strip()
    if not q:
        return {'ok': True, 'results': []}
    inst = installed_ids()
    hits = []
    try:
        req = urllib.request.Request('https://flathub.org/api/v2/search', data=json.dumps({'query': q}).encode(),
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'Rise/0.5'})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        for h in (d.get('hits') or [])[:24]:
            i = h.get('app_id') or h.get('id')
            if not i:
                continue
            hits.append({'id': i, 'name': h.get('name') or i, 'desc': h.get('summary') or '',
                         'icon': h.get('icon') or ICON % i, 'installed': i in inst})
    except Exception:
        # riserva: la ricerca di flatpak sul catalogo già scaricato
        rc, out, _ = _run(['flatpak', 'search', '--columns=application,name,description', q], 30)
        for l in out.splitlines()[:24]:
            p = l.split('\t')
            if len(p) >= 2 and '.' in p[0]:
                hits.append({'id': p[0], 'name': p[1], 'desc': p[2] if len(p) > 2 else '', 'icon': ICON % p[0],
                             'installed': p[0] in inst})
    return {'ok': True, 'results': hits}


def _register(app_id, name):
    """L'app installata compare nel menu Start di Rise."""
    try:
        apps = json.load(open(APPS_FILE))
    except Exception:
        apps = []
    apps = [x for x in apps if x.get('id') != 'fp:' + app_id]
    icon = None
    for base in ('/var/lib/flatpak/exports/share/icons/hicolor', os.path.expanduser('~/.local/share/flatpak/exports/share/icons/hicolor')):
        for size in ('128x128', '256x256', '64x64', 'scalable'):
            for ext in ('png', 'svg'):
                p = os.path.join(base, size, 'apps', '%s.%s' % (app_id, ext))
                if os.path.exists(p):
                    icon = icon or p
    apps.append({'id': 'fp:' + app_id, 'name': name, 'desc': 'App installata dal Negozio',
                 'cmd': ['flatpak', 'run', app_id], 'img': icon or ICON % app_id, 'app_ids': [app_id]})
    os.makedirs(os.path.dirname(APPS_FILE), exist_ok=True)
    json.dump(apps, open(APPS_FILE, 'w'))


def _unregister(app_id):
    try:
        apps = json.load(open(APPS_FILE))
        json.dump([x for x in apps if x.get('id') != 'fp:' + app_id], open(APPS_FILE, 'w'))
    except Exception:
        pass


def _install(app_id, name):
    j = _jobs[app_id]
    try:
        _run(['sudo', '-n', 'flatpak', 'remote-add', '--if-not-exists', 'flathub',
              'https://dl.flathub.org/repo/flathub.flatpakrepo'], 60)
        p = subprocess.Popen(['sudo', '-n', 'flatpak', 'install', '-y', '--noninteractive', 'flathub', app_id],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        last = ''
        for line in p.stdout:
            line = line.strip()
            if line:
                last = line
            m = re.search(r'(\d{1,3})%', line)
            if m:
                j['pct'] = int(m.group(1))
            if 'Installing' in line or 'Installazione' in line:
                j['msg'] = 'Installo…'
            elif 'Downloading' in line or 'Scaricamento' in line:
                j['msg'] = 'Scarico…'
        p.wait()
        if p.returncode != 0 and app_id not in installed_ids():
            raise RuntimeError(last[-200:] or 'installazione non riuscita')
        _register(app_id, name)
        j.update(pct=100, msg='Installata')
    except Exception as e:
        j['error'] = 'Non sono riuscito a installarla: %s' % e
    finally:
        j['running'] = False


def install(a):
    app_id = (a.get('id') or '').strip()
    if not re.fullmatch(r'[\w.-]+', app_id):
        return {'ok': False, 'message': 'App non valida.'}
    if not _flatpak():
        return {'ok': False, 'message': 'Il Negozio non è disponibile su questa versione di Rise.'}
    with _lock:
        if (_jobs.get(app_id) or {}).get('running'):
            return {'ok': True}
        _jobs[app_id] = {'running': True, 'pct': 0, 'msg': 'Preparo…', 'error': None}
    threading.Thread(target=_install, args=(app_id, a.get('name') or app_id), daemon=True).start()
    return {'ok': True}


def remove(a):
    app_id = (a.get('id') or '').strip()
    if not re.fullmatch(r'[\w.-]+', app_id):
        return {'ok': False}
    rc, _, err = _run(['sudo', '-n', 'flatpak', 'uninstall', '-y', '--noninteractive', app_id], 300)
    _unregister(app_id)
    return {'ok': rc == 0, 'message': err.strip()[-200:] if rc else ''}


def launch(a):
    app_id = (a.get('id') or '').strip()
    if not re.fullmatch(r'[\w.-]+', app_id):
        return {'ok': False}
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    subprocess.Popen(['flatpak', 'run', app_id], start_new_session=True, env=env,
                     stdout=subprocess.DEVNULL, stderr=open('/tmp/rise-app-%s.log' % app_id, 'a'))
    return {'ok': True}


def jobs(_=None):
    return {'ok': True, 'jobs': {k: dict(v) for k, v in _jobs.items()}, 'installed': list(installed_ids())}


CMDS = {'store.catalog': catalog, 'store.search': search, 'store.install': install, 'store.remove': remove,
        'store.launch': launch, 'store.jobs': jobs}
