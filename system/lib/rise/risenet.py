"""Rise – strumenti di rete: radar dei dispositivi sulla rete di casa/ufficio, ping, porte aperte, DNS."""
import concurrent.futures as cf
import ipaddress
import os
import re
import socket
import subprocess
import time

# porte che fanno capire che cos'è un dispositivo
PORTS = {80: 'web', 443: 'web sicuro', 22: 'SSH', 445: 'cartelle condivise', 139: 'cartelle condivise',
         631: 'stampante (IPP)', 9100: 'stampante', 515: 'stampante (LPD)', 554: 'videocamera (RTSP)',
         3389: 'desktop remoto', 5900: 'schermo remoto (VNC)', 8080: 'web', 53317: 'Rise Drop / LocalSend',
         62078: 'iPhone/iPad', 8009: 'Chromecast', 1883: 'domotica (MQTT)', 8123: 'Home Assistant', 5000: 'NAS'}
_OUI = {}


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, '', str(e)


def _vendor(mac):
    """Marca del dispositivo dal codice della scheda di rete (elenco IEEE, se installato)."""
    if not mac:
        return ''
    if not _OUI:
        for f in ('/usr/share/ieee-data/oui.txt', '/usr/share/misc/oui.txt'):
            try:
                for line in open(f, errors='replace'):
                    m = re.match(r'^([0-9A-F]{2})-([0-9A-F]{2})-([0-9A-F]{2})\s+\(hex\)\s+(.+)$', line.strip())
                    if m:
                        _OUI[''.join(m.groups()[:3])] = m.group(4).strip()
                break
            except OSError:
                continue
        _OUI.setdefault('_', '')
    key = mac.upper().replace(':', '')[:6]
    if int(key[1], 16) & 2:                       # indirizzo "casuale" (telefoni moderni)
        return 'indirizzo privato (telefono o tablet)'
    return _OUI.get(key, '')


def _local_net():
    """La rete a cui sei collegato: (indirizzo del PC, rete /24, router)."""
    rc, out, _ = _run(['ip', '-4', 'route', 'get', '1.1.1.1'])
    m = re.search(r'src (\d+\.\d+\.\d+\.\d+)', out)
    gw = re.search(r'via (\d+\.\d+\.\d+\.\d+)', out)
    if not m:
        return None, None, None
    me = m.group(1)
    rc, out, _ = _run(['ip', '-4', '-o', 'addr'])
    net = None
    for line in out.splitlines():
        mm = re.search(r'inet (%s)/(\d+)' % re.escape(me), line)
        if mm:
            pref = max(int(mm.group(2)), 24)        # al massimo 254 indirizzi: veloce e senza intasare la rete
            net = ipaddress.ip_network('%s/%d' % (me, pref), strict=False)
    return me, net, gw.group(1) if gw else None


def _ping1(ip, wait=1):
    rc, out, _ = _run(['ping', '-c', '1', '-W', str(wait), '-q', str(ip)], timeout=wait + 2)
    return rc == 0


def _probe(ip, ports=PORTS, timeout=0.35):
    found = []
    for p in ports:
        s = socket.socket()
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, p)) == 0:
                found.append(p)
        except OSError:
            pass
        finally:
            s.close()
    return found


def _neigh():
    rc, out, _ = _run(['ip', 'neigh'])
    res = {}
    for line in out.splitlines():
        m = re.match(r'(\d+\.\d+\.\d+\.\d+) .*lladdr ([0-9a-f:]{17}) (\w+)', line)
        if m and m.group(3) not in ('FAILED', 'INCOMPLETE'):
            res[m.group(1)] = m.group(2)
    return res


def _mdns():
    """Nomi annunciati in rete (stampanti, Mac, telefoni, TV…)."""
    rc, out, _ = _run(['avahi-browse', '-a', '-r', '-t', '-p'], timeout=6)
    res = {}
    for line in out.splitlines():
        f = line.split(';')
        if len(f) > 7 and f[0] == '=' and f[2] == 'IPv4':
            name, typ, ip = f[3], f[4], f[7]
            d = res.setdefault(ip, {'names': set(), 'services': set()})
            d['names'].add(name.replace('\\032', ' ').split('@')[-1].strip())
            d['services'].add(typ)
    return res


def _kind(ports, services, vendor, name):
    t = ' '.join(services).lower() + ' ' + (vendor or '').lower() + ' ' + (name or '').lower()
    if 631 in ports or 9100 in ports or 'ipp' in t or 'printer' in t or any(x in t for x in ('epson', 'canon', 'brother', 'hp ', 'hewlett', 'kyocera', 'xerox')):
        return 'stampante'
    if 'airplay' in t or 'googlecast' in t or 8009 in ports or any(x in t for x in ('samsung', 'lg electronics', 'sony', 'roku')) and 554 not in ports:
        return 'TV / multimediale'
    if 554 in ports or 'camera' in t or 'hikvision' in t or 'dahua' in t:
        return 'videocamera'
    if 62078 in ports or 'apple' in t or 'privato' in t or 'android' in t:
        return 'telefono / tablet'
    if 5000 in ports and ('synology' in t or 'qnap' in t) or 'nas' in t:
        return 'NAS (archivio di rete)'
    if 445 in ports or 3389 in ports or 22 in ports or 53317 in ports:
        return 'computer'
    if 80 in ports or 443 in ports:
        return 'dispositivo con pagina web'
    return 'dispositivo'


def radar(_=None):
    me, net, gw = _local_net()
    if not net:
        return {'ok': False, 'message': 'Non sei collegato a nessuna rete.'}
    t0 = time.time()
    hosts = [str(h) for h in net.hosts()]
    with cf.ThreadPoolExecutor(64) as ex:
        alive = {ip for ip, ok in zip(hosts, ex.map(_ping1, hosts)) if ok}
    neigh = _neigh()
    alive |= {ip for ip in neigh if ipaddress.ip_address(ip) in net}     # chi non risponde al ping ma c'è
    alive.add(me)
    mdns = _mdns()
    with cf.ThreadPoolExecutor(32) as ex:
        ports = dict(zip(sorted(alive), ex.map(_probe, sorted(alive))))
    out = []
    for ip in sorted(alive, key=lambda x: tuple(int(p) for p in x.split('.'))):
        try:
            host = socket.gethostbyaddr(ip)[0]
        except OSError:
            host = ''
        m = mdns.get(ip, {})
        name = sorted(m.get('names') or [''])[0] or host.split('.')[0]
        vendor = _vendor(neigh.get(ip, ''))
        kind = 'questo computer' if ip == me else 'router (internet)' if ip == gw else _kind(ports.get(ip, []), m.get('services') or [], vendor, name)
        out.append({'ip': ip, 'name': name, 'host': host, 'mac': neigh.get(ip, ''), 'vendor': vendor, 'kind': kind,
                    'ports': [{'port': p, 'what': PORTS[p]} for p in ports.get(ip, [])]})
    return {'ok': True, 'me': me, 'gateway': gw, 'network': str(net), 'devices': out, 'seconds': round(time.time() - t0, 1)}


def ping(a):
    host = (a.get('host') or '').strip()
    if not re.fullmatch(r'[\w.:-]{1,253}', host):
        return {'ok': False, 'message': 'Scrivi un indirizzo valido, per esempio google.it o 192.168.1.1'}
    n = max(1, min(20, int(a.get('count') or 4)))
    rc, out, err = _run(['ping', '-c', str(n), '-W', '2', host], timeout=n * 3 + 5)
    times = [float(x) for x in re.findall(r'time=([\d.]+)', out)]
    m = re.search(r'(\d+) packets transmitted, (\d+) received', out)
    sent, recv = (int(m.group(1)), int(m.group(2))) if m else (n, len(times))
    ip = re.search(r'PING [^(]+\(([\d.:a-f]+)\)', out)
    verdict = ('Non raggiungibile' if recv == 0 else 'Ottimo' if max(times or [0]) < 40 and recv == sent
               else 'Buono' if (sum(times) / len(times)) < 100 and recv >= sent - 1 else 'Lento o instabile')
    return {'ok': recv > 0, 'host': host, 'ip': ip.group(1) if ip else '', 'sent': sent, 'received': recv,
            'times': times, 'avg': round(sum(times) / len(times), 1) if times else None,
            'min': min(times) if times else None, 'max': max(times) if times else None, 'verdict': verdict,
            'message': (err or out).strip().splitlines()[-1] if recv == 0 and (err or out).strip() else ''}


def ports(a):
    host = (a.get('host') or '').strip()
    if not re.fullmatch(r'[\w.:-]{1,253}', host):
        return {'ok': False, 'message': 'Indirizzo non valido.'}
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return {'ok': False, 'message': 'Non trovo “%s”.' % host}
    found = _probe(ip, timeout=0.6)
    return {'ok': True, 'host': host, 'ip': ip, 'open': [{'port': p, 'what': PORTS[p]} for p in found]}


def dns(a):
    host = (a.get('host') or '').strip()
    try:
        infos = socket.getaddrinfo(host, None)
        ips = sorted({i[4][0] for i in infos})
        return {'ok': True, 'host': host, 'ips': ips}
    except OSError as e:
        return {'ok': False, 'message': 'Il nome “%s” non si trova (%s).' % (host, e)}


CMDS = {'net.radar': radar, 'net.ping': ping, 'net.ports': ports, 'net.dns': dns}
