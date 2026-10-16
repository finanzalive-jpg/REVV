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
        return {'ok': True, 'url': final, 'title': '', 'text': page[:8000]}
    t = re.search(r'<title[^>]*>([\s\S]*?)</title>', page, re.I)
    body = re.sub(r'(?is)<(script|style|noscript|svg|nav|footer|header|form|aside|iframe)[^>]*>.*?</\1>', ' ', page)
    body = re.sub(r'(?i)<(br|/p|/div|/li|/h[1-6]|/tr|/section|/article)[^>]*>', '\n', body)
    body = re.sub(r'(?i)<li[^>]*>', '\n- ', body)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', body))
    lines = [re.sub(r'[ \t\xa0]+', ' ', l).strip() for l in text.split('\n')]
    lines = [l for l in lines if len(l) > 2]
    out = '\n'.join(lines)
    out = re.sub(r'\n{3,}', '\n\n', out)
    return {'ok': True, 'url': final, 'title': _text(t.group(1)) if t else '', 'text': out[:8000]}


CMDS = {'web.search': search, 'web.read': read}
