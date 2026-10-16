"""Rise – l'Assistente su internet: cerca sul web e legge le pagine.

La ricerca usa DuckDuckGo (versione HTML, senza account e senza chiavi); le pagine vengono
scaricate e ridotte a testo semplice. Tutto passa dal computer dell'utente: nessun servizio
di Rise in mezzo.
"""
import html
import re
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'


def _get(url, data=None, timeout=15, limit=2_000_000):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept-Language': 'it-IT,it;q=0.9,en;q=0.5'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit)
        ctype = r.headers.get('Content-Type', '')
        m = re.search(r'charset=([\w-]+)', ctype)
        enc = m.group(1) if m else 'utf-8'
        return raw.decode(enc, 'replace'), ctype, r.geturl()


def _text(fragment):
    return html.unescape(re.sub(r'<[^>]+>', '', fragment)).strip()


def _real_url(u):
    u = html.unescape(u)
    if u.startswith('//'):
        u = 'https:' + u
    q = urllib.parse.urlparse(u)
    if 'duckduckgo.com' in q.netloc and q.path.startswith('/l/'):
        uddg = urllib.parse.parse_qs(q.query).get('uddg')
        if uddg:
            return uddg[0]
    return u


def search(a):
    q = (a.get('q') or a.get('query') or '').strip()
    if not q:
        return {'ok': False, 'message': 'Cosa devo cercare?'}
    results = []
    try:
        page, _, _ = _get('https://html.duckduckgo.com/html/', urllib.parse.urlencode({'q': q, 'kl': 'it-it'}).encode())
        blocks = re.split(r'(?=<a\b[^>]*class="result__a")', page)[1:]
        for b in blocks:
            tag = re.match(r'<a\b[^>]*>', b).group(0)
            h = re.search(r'href="([^"]+)"', tag)
            t = re.search(r'<a\b[^>]*>([\s\S]*?)</a>', b)
            if not h or not t:
                continue
            url = _real_url(h.group(1))
            if 'duckduckgo.com/y.js' in url or 'ad_domain' in url:      # pubblicità
                continue
            sn = re.search(r'class="result__snippet"[^>]*>([\s\S]*?)</a>', b)
            results.append({'title': _text(t.group(1)), 'url': url, 'snippet': _text(sn.group(1)) if sn else ''})
            if len(results) >= 6:
                break
        if not results:                                # riserva: versione "lite"
            page, _, _ = _get('https://lite.duckduckgo.com/lite/', urllib.parse.urlencode({'q': q, 'kl': 'it-it'}).encode())
            links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class=.result-link.[^>]*>([\s\S]*?)</a>', page)
            snips = re.findall(r'class=.result-snippet.[^>]*>([\s\S]*?)</td>', page)
            for i, (u, t) in enumerate(links[:6]):
                results.append({'title': _text(t), 'url': _real_url(u), 'snippet': _text(snips[i]) if i < len(snips) else ''})
    except Exception as e:
        return {'ok': False, 'message': 'Non riesco a cercare su internet: controlla la connessione. (%s)' % e}
    return {'ok': True, 'results': results}


def read(a):
    url = (a.get('url') or '').strip()
    if not re.match(r'https?://', url):
        url = 'https://' + url
    try:
        page, ctype, final = _get(url)
    except Exception as e:
        return {'ok': False, 'message': 'Non riesco ad aprire la pagina. (%s)' % e}
    if 'html' not in ctype and '<html' not in page[:500].lower():
        return {'ok': True, 'url': final, 'title': '', 'text': page[:3500]}
    t = re.search(r'<title[^>]*>([\s\S]*?)</title>', page, re.I)
    body = re.sub(r'(?is)<(script|style|noscript|svg|nav|footer|header|form|aside|iframe)[^>]*>.*?</\1>', ' ', page)
    body = re.sub(r'(?i)<(br|/p|/div|/li|/h[1-6]|/tr|/section|/article)[^>]*>', '\n', body)
    body = re.sub(r'(?i)<li[^>]*>', '\n- ', body)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', body))
    lines = [re.sub(r'[ \t\xa0]+', ' ', l).strip() for l in text.split('\n')]
    lines = [l for l in lines if len(l) > 2]
    out = '\n'.join(lines)
    out = re.sub(r'\n{3,}', '\n\n', out)
    return {'ok': True, 'url': final, 'title': _text(t.group(1)) if t else '', 'text': out[:3500]}


_WMO = {0: 'sereno', 1: 'quasi sereno', 2: 'poco nuvoloso', 3: 'nuvoloso', 45: 'nebbia', 48: 'nebbia gelata',
        51: 'pioggerella debole', 53: 'pioggerella', 55: 'pioggerella intensa', 61: 'pioggia debole', 63: 'pioggia',
        65: 'pioggia forte', 66: 'pioggia gelata', 67: 'pioggia gelata forte', 71: 'neve debole', 73: 'neve',
        75: 'neve forte', 77: 'nevischio', 80: 'rovesci deboli', 81: 'rovesci', 82: 'rovesci forti',
        85: 'rovesci di neve', 86: 'forti rovesci di neve', 95: 'temporale', 96: 'temporale con grandine',
        99: 'forte temporale con grandine'}
_GIORNI = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']


def weather(a):
    """Previsioni del tempo da Open-Meteo (gratis, senza account): dati veri, in un secondo."""
    import json
    import datetime
    city = (a.get('citta') or a.get('city') or '').strip()
    if not city:
        return {'ok': False, 'message': 'Per quale città?'}
    try:
        # "Milano, Italia", "Milano (MI)", "milano domani"…: si prova il nome completo, poi la parte prima
        # della virgola, poi la prima parola; tra i risultati si preferiscono le città italiane.
        tries, res = [], []
        for c0 in (city, re.split(r'[,(]', city)[0], city.split()[0]):
            c0 = c0.strip()
            if c0 and c0 not in tries:
                tries.append(c0)
        for c0 in tries:
            g, _, _ = _get('https://geocoding-api.open-meteo.com/v1/search?' +
                           urllib.parse.urlencode({'name': c0, 'count': 5, 'language': 'it', 'format': 'json'}))
            res = json.loads(g).get('results') or []
            if res:
                res.sort(key=lambda r: (r.get('country_code') != 'IT', -(r.get('population') or 0)))
                break
        if not res:
            return {'ok': False, 'message': 'Non trovo la città “%s”.' % city}
        c = res[0]
        q = {'latitude': c['latitude'], 'longitude': c['longitude'], 'timezone': 'auto', 'forecast_days': 7,
             'current': 'temperature_2m,weather_code,wind_speed_10m',
             'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max'}
        f, _, _ = _get('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(q))
        d = json.loads(f)
    except Exception as e:
        return {'ok': False, 'message': 'Non riesco a leggere le previsioni: controlla la connessione. (%s)' % e}
    name = c.get('name', city) + (', ' + c['admin1'] if c.get('admin1') else '')
    lines = ['Previsioni per %s (fonte: Open-Meteo)' % name]
    cur = d.get('current') or {}
    if cur:
        lines.append('Adesso: %s, %s °C, vento %s km/h' % (_WMO.get(cur.get('weather_code'), '?'),
                                                         round(cur.get('temperature_2m', 0)), round(cur.get('wind_speed_10m', 0))))
    dy = d.get('daily') or {}
    today = datetime.date.today()
    days = []
    for i, day in enumerate(dy.get('time', [])):
        dt = datetime.date.fromisoformat(day)
        label = 'oggi' if dt == today else 'domani' if dt == today + datetime.timedelta(days=1) else ''
        lines.append('%s %d/%d%s: %s, min %s °C, max %s °C, pioggia %s%%, vento fino a %s km/h' % (
            _GIORNI[dt.weekday()], dt.day, dt.month, ' (%s)' % label if label else '',
            _WMO.get(dy['weather_code'][i], '?'), round(dy['temperature_2m_min'][i]), round(dy['temperature_2m_max'][i]),
            dy['precipitation_probability_max'][i] if dy.get('precipitation_probability_max') else '?',
            round(dy['wind_speed_10m_max'][i])))
        days.append({'date': day, 'weekday': _GIORNI[dt.weekday()], 'label': label,
                     'desc': _WMO.get(dy['weather_code'][i], '?'), 'min': round(dy['temperature_2m_min'][i]),
                     'max': round(dy['temperature_2m_max'][i]),
                     'rain': dy['precipitation_probability_max'][i] if dy.get('precipitation_probability_max') else None})
    now = {'desc': _WMO.get(cur.get('weather_code'), '?'), 'temp': round(cur.get('temperature_2m', 0))} if cur else None
    return {'ok': True, 'text': '\n'.join(lines), 'name': c.get('name', city), 'now': now, 'days': days}


CMDS = {'web.search': search, 'web.read': read, 'web.weather': weather}
