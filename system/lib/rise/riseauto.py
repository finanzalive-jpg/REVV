"""Rise – azioni dell'Assistente sul computer: cartelle, file, PDF, email e rubrica.

Tutto quello che esce dal computer (le email) parte solo dopo la conferma dell'utente: l'Assistente
prepara la bozza, Rise la mostra, l'utente dice "invia" o preme il pulsante.
"""
import glob
import json
import os
import re
import smtplib
import subprocess
import sys
import time
import unicodedata
import zipfile
from email.message import EmailMessage
from xml.sax.saxutils import unescape

HOME = os.path.expanduser('~')
CONF = os.path.join(HOME, '.config', 'rise')
MAIL_FILE = os.path.join(CONF, 'posta.json')
CONTACTS_FILE = os.path.join(CONF, 'rubrica.json')
for _p in glob.glob('/opt/rise-py/lib/python3*/site-packages'):     # fpdf2 (licenza LGPL) per i PDF
    if _p not in sys.path:
        sys.path.append(_p)

PLACES = {'documenti': 'Documenti', 'desktop': 'Scrivania', 'scrivania': 'Scrivania', 'download': 'Download',
          'scaricati': 'Download', 'immagini': 'Immagini', 'foto': 'Immagini', 'musica': 'Musica', 'video': 'Video',
          'home': '', 'casa': ''}
KINDS = {'word': ('.docx', '.doc', '.odt', '.rtf'), 'documento': ('.docx', '.doc', '.odt', '.rtf', '.pdf', '.txt'),
         'excel': ('.xlsx', '.xls', '.ods', '.csv'), 'foglio': ('.xlsx', '.xls', '.ods', '.csv'),
         'pdf': ('.pdf',), 'presentazione': ('.pptx', '.ppt', '.odp'),
         'foto': ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.gif'), 'immagine': ('.jpg', '.jpeg', '.png', '.webp', '.gif'),
         'video': ('.mp4', '.mkv', '.avi', '.mov', '.webm'), 'musica': ('.mp3', '.flac', '.ogg', '.m4a', '.wav')}


def _norm(s):
    s = unicodedata.normalize('NFD', s.lower())
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def _place(where):
    w = _norm(where or 'documenti').strip()
    for k, v in PLACES.items():
        if k in w:
            return os.path.join(HOME, v) if v else HOME
    if w.startswith('/') and os.path.isdir(w):
        return w
    return os.path.join(HOME, 'Documenti')


# ---------------- cartelle ----------------
def make_folder(a):
    name = re.sub(r'[\\/:*?"<>|]+', ' ', (a.get('nome') or a.get('name') or '').strip())[:80].strip()
    if not name:
        return {'ok': False, 'message': 'Come la chiamo?'}
    base = _place(a.get('dove') or a.get('where'))
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, name)
    existed = os.path.isdir(path)
    os.makedirs(path, exist_ok=True)
    return {'ok': True, 'path': path, 'existed': existed,
            'where': os.path.basename(base) or 'cartella personale'}


# ---------------- trovare i file ----------------
SKIP = {'.cache', '.local', '.config', '.mozilla', 'node_modules', '__pycache__', '.git', 'snap'}


def find_files(a):
    q = _norm(a.get('testo') or a.get('q') or '')
    kind = _norm(a.get('tipo') or '')
    exts = None
    for k, v in KINDS.items():
        if k in kind or (not kind and re.search(r'\b%s\b' % k, q)):
            exts = v
            q = re.sub(r'\b(il |la |lo |l\')?(file |documento )?%s\b' % k, ' ', q)
            break
    stop = {'il', 'la', 'lo', 'le', 'i', 'gli', 'un', 'una', 'di', 'del', 'della', 'file', 'documento', 'apri',
            'aprimi', 'mi', 'che', 'ho', 'fatto', 'ultimo', 'ultima', 'mio', 'mia', 'per', 'a', 'al', 'con'}
    words = [w for w in re.findall(r'[\w]+', q) if w not in stop and len(w) > 1]
    latest = bool(re.search(r'\bultim', _norm(a.get('testo') or '')))
    roots = [HOME] + [d for d in glob.glob('/media/*/*') if os.path.isdir(d)]
    found, t0 = [], time.time()
    for root in roots:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if not d.startswith('.') and d not in SKIP]
            if dp.count(os.sep) - root.count(os.sep) > 6 or time.time() - t0 > 4:
                dns[:] = []
            for f in fns:
                if f.startswith('.'):
                    continue
                low = _norm(f)
                if exts and not low.endswith(exts):
                    continue
                if words and not all(w in low for w in words):
                    continue
                if not words and not exts:
                    continue
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                found.append({'path': p, 'name': f, 'size': st.st_size, 'mtime': int(st.st_mtime * 1000),
                              'folder': dp.replace(HOME, '~', 1)})
    found.sort(key=lambda x: -x['mtime'])
    if latest:
        found = found[:1]
    return {'ok': True, 'results': found[:8]}


# ---------------- PDF ----------------
def _font_paths():
    for base in ('/usr/share/fonts/truetype/liberation', '/usr/share/fonts/truetype/liberation2'):
        r, b = os.path.join(base, 'LiberationSans-Regular.ttf'), os.path.join(base, 'LiberationSans-Bold.ttf')
        if os.path.exists(r) and os.path.exists(b):
            return r, b
    return None, None


def _pdf_from_markup(text, path, title=''):
    from fpdf import FPDF
    pdf = FPDF(format='A4')
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, 18)
    reg, bold = _font_paths()
    if reg:
        pdf.add_font('Rise', '', reg)
        pdf.add_font('Rise', 'B', bold)
        fam = 'Rise'
    else:
        fam = 'Helvetica'
    pdf.set_title(title or 'Documento')
    pdf.set_creator('Rise')
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    def para(t, size=11, style='', color=(26, 26, 26), after=2.5):
        pdf.set_font(fam, style, size)
        pdf.set_text_color(*color)
        pdf.multi_cell(W, size * 0.5, t.replace('**', ''), markdown=False, new_x='LMARGIN', new_y='NEXT')
        pdf.ln(after)

    def rich(t):
        # grassetto **…** dentro la riga
        pdf.set_text_color(26, 26, 26)
        parts = re.split(r'(\*\*[^*]+\*\*)', t)
        for part in parts:
            if not part:
                continue
            b = part.startswith('**') and part.endswith('**')
            pdf.set_font(fam, 'B' if b else '', 11)
            pdf.write(5.5, part[2:-2] if b else part)
        pdf.ln(7.5)

    table = []

    def flush():
        if not table:
            return
        cols = max(len(r) for r in table)
        widths = [W * 0.49, W * 0.09, W * 0.21, W * 0.21] if cols == 4 else [W / cols] * cols
        pdf.set_draw_color(154, 167, 184)
        for i, r in enumerate(table):
            pdf.set_font(fam, 'B' if i == 0 else '', 10)
            if i == 0:
                pdf.set_fill_color(220, 232, 250)
            h = 7
            for j in range(cols):
                c = (r[j] if j < len(r) else '').strip()
                bold = c.startswith('**')
                c = c.replace('**', '')
                if bold:
                    pdf.set_font(fam, 'B', 10)
                empty = not c and i > 0
                pdf.cell(widths[j], h, c, border=0 if empty else 1, fill=(i == 0),
                         align='R' if j and re.fullmatch(r'[-+€$\s\d.,%]+', c or 'x') else 'L')
                if bold and i:
                    pdf.set_font(fam, '', 10)
            pdf.ln(h)
        pdf.ln(4)
        table.clear()

    for raw in (text or '').replace('\r', '').split('\n'):
        l = raw.strip()
        if l.startswith('|') and l.endswith('|') and len(l) > 1:
            cells = l[1:-1].split('|')
            if all(re.fullmatch(r'\s*:?-{2,}:?\s*', c) for c in cells):
                continue
            table.append(cells)
            continue
        flush()
        m = re.match(r'(#{1,3})\s+(.*)', l)
        if m:
            n = len(m.group(1))
            pdf.ln(2 if n > 1 else 0)
            para(m.group(2), {1: 18, 2: 14, 3: 12}[n], 'B', (31, 78, 158) if n == 1 else (36, 86, 224) if n == 2 else (40, 40, 40), 2)
        elif re.match(r'[-*•]\s+', l):
            pdf.set_x(pdf.l_margin + 4)
            rich('•  ' + re.sub(r'^[-*•]\s+', '', l))
        elif re.fullmatch(r'-{3,}|\*{3,}|_{3,}', l):
            y = pdf.get_y() + 2
            pdf.set_draw_color(154, 167, 184)
            pdf.line(pdf.l_margin, y, pdf.l_margin + W, y)
            pdf.ln(5)
        elif not l:
            pdf.ln(3)
        else:
            rich(l)
    flush()
    pdf.output(path)


def _docx_text(path):
    """Testo (con titoli, elenchi e tabelle) di un .docx, per trasformarlo in PDF."""
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml').decode('utf8', 'replace')
    out = []
    body = re.search(r'<w:body>(.*)</w:body>', xml, re.S).group(1)
    for m in re.finditer(r'<w:tbl>.*?</w:tbl>|<w:p[ >].*?</w:p>|<w:p/>', body, re.S):
        blk = m.group(0)
        if blk.startswith('<w:tbl>'):
            for tr in re.findall(r'<w:tr[ >].*?</w:tr>', blk, re.S):
                cells = []
                for tc in re.findall(r'<w:tc>.*?</w:tc>', tr, re.S):
                    cells.append(unescape(''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', tc))))
                out.append('| ' + ' | '.join(cells) + ' |')
            out.append('')
            continue
        st = re.search(r'<w:pStyle w:val="([^"]+)"', blk)
        txt = ''
        for r in re.findall(r'<w:r[ >].*?</w:r>', blk, re.S):
            t = unescape(''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', r)))
            if t and '<w:b/>' in r and t.strip():
                t = '**%s**' % t
            txt += t
        s = st.group(1).lower() if st else ''
        if re.search(r'(titolo|heading)\s*1|^title', s):
            txt = '# ' + txt.replace('**', '')
        elif re.search(r'(titolo|heading)\s*2', s):
            txt = '## ' + txt.replace('**', '')
        elif re.search(r'(titolo|heading)\s*3', s):
            txt = '### ' + txt.replace('**', '')
        elif '<w:numPr>' in blk or 'elenco' in s or 'list' in s:
            txt = '- ' + txt
        out.append(txt)
    return '\n'.join(out)


def make_pdf(a):
    """Crea un PDF dal testo dell'Assistente, oppure trasforma in PDF un documento Word."""
    try:
        import fpdf  # noqa: F401
    except ImportError:
        return {'ok': False, 'message': 'Su questa versione di Rise manca il componente per i PDF.'}
    src = a.get('da_file') or a.get('path')
    title = (a.get('titolo') or a.get('title') or '').strip()
    if src:
        src = os.path.expanduser(src)
        if not os.path.isfile(src):
            return {'ok': False, 'message': 'Non trovo il file da trasformare.'}
        if src.lower().endswith('.pdf'):
            return {'ok': True, 'path': src, 'name': os.path.basename(src)}
        if not src.lower().endswith('.docx'):
            return {'ok': False, 'message': 'Per ora so trasformare in PDF solo i documenti Word (.docx).'}
        text = _docx_text(src)
        out = os.path.splitext(src)[0] + '.pdf'
        title = title or os.path.splitext(os.path.basename(src))[0]
    else:
        text = a.get('testo') or a.get('text') or ''
        if not title:
            m = re.search(r'^#{1,3}\s+(.+)$', text, re.M)
            title = m.group(1) if m else 'Documento'
        title = re.sub(r'[\\/:*?"<>|\n#]+', ' ', title.replace('**', '')).strip()[:70] or 'Documento'
        folder = os.path.join(HOME, 'Documenti')
        os.makedirs(folder, exist_ok=True)
        out = os.path.join(folder, title + '.pdf')
    n, base = 2, out[:-4]
    while os.path.exists(out) and not src:
        out = '%s (%d).pdf' % (base, n)
        n += 1
    _pdf_from_markup(text, out, title)
    return {'ok': True, 'path': out, 'name': os.path.basename(out), 'size': os.path.getsize(out)}


# ---------------- rubrica ----------------
def _contacts():
    try:
        return json.load(open(CONTACTS_FILE))
    except Exception:
        return []


def contacts(_=None):
    return {'ok': True, 'contacts': _contacts()}


def contact_save(a):
    name, email = (a.get('nome') or '').strip(), (a.get('email') or '').strip()
    if not name or not re.fullmatch(r'[^@\s]+@[^@\s]+\.\w+', email):
        return {'ok': False, 'message': 'Servono un nome e un indirizzo email valido.'}
    c = [x for x in _contacts() if _norm(x['nome']) != _norm(name)]
    c.append({'nome': name, 'email': email})
    os.makedirs(CONF, exist_ok=True)
    json.dump(sorted(c, key=lambda x: _norm(x['nome'])), open(CONTACTS_FILE, 'w'), ensure_ascii=False)
    return {'ok': True}


def contact_delete(a):
    c = [x for x in _contacts() if _norm(x['nome']) != _norm(a.get('nome') or '')]
    json.dump(c, open(CONTACTS_FILE, 'w'), ensure_ascii=False)
    return {'ok': True}


def resolve_address(who):
    who = (who or '').strip()
    if re.fullmatch(r'[^@\s]+@[^@\s]+\.\w+', who):
        return who, who
    n = _norm(who)
    best = None
    for c in _contacts():
        cn = _norm(c['nome'])
        if cn == n:
            return c['email'], c['nome']
        if n and (n in cn or all(w in cn for w in n.split())):
            best = best or c
    return (best['email'], best['nome']) if best else (None, who)


# ---------------- posta ----------------
PROVIDERS = {
    'gmail': ('smtp.gmail.com', 587, 'Gmail'),
    'outlook': ('smtp-mail.outlook.com', 587, 'Outlook / Hotmail'),
    'libero': ('smtp.libero.it', 465, 'Libero'),
    'aruba': ('smtps.aruba.it', 465, 'Aruba'),
    'arubapec': ('smtps.pec.aruba.it', 465, 'Aruba PEC'),
    'tim': ('smtp.tim.it', 587, 'TIM'),
    'yahoo': ('smtp.mail.yahoo.com', 587, 'Yahoo'),
}


def _mail_conf():
    try:
        return json.load(open(MAIL_FILE))
    except Exception:
        return {}


def mail_get(_=None):
    c = _mail_conf()
    tb = any(os.path.exists(p) for p in ('/var/lib/flatpak/app/org.mozilla.Thunderbird',
                                          os.path.join(HOME, '.local/share/flatpak/app/org.mozilla.Thunderbird')))
    return {'ok': True, 'configured': bool(c.get('email') and c.get('password')), 'email': c.get('email', ''),
            'name': c.get('name', ''), 'provider': c.get('provider', 'gmail'), 'thunderbird': tb,
            'providers': {k: v[2] for k, v in PROVIDERS.items()}}


def mail_set(a):
    c = _mail_conf()
    for k in ('email', 'name', 'provider', 'host', 'port'):
        if a.get(k) is not None:
            c[k] = a[k]
    if a.get('password'):
        c['password'] = a['password'].replace(' ', '')     # le password per le app di Google hanno spazi
    os.makedirs(CONF, exist_ok=True)
    fd = os.open(MAIL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # leggibile solo dall'utente
    with os.fdopen(fd, 'w') as f:
        json.dump(c, f)
    return {'ok': True}


def _smtp_send(c, msg):
    host, port = c.get('host'), int(c.get('port') or 0)
    if not host:
        host, port, _ = PROVIDERS.get(c.get('provider', 'gmail'), PROVIDERS['gmail'])
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        s = smtplib.SMTP(host, port, timeout=30)
        s.starttls()
    try:
        s.login(c['email'], c['password'])
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:
            pass


def mail_send(a):
    """Invia davvero l'email (lo chiama il desktop solo dopo la conferma dell'utente)."""
    c = _mail_conf()
    to, label = resolve_address(a.get('a') or a.get('to'))
    if not to:
        return {'ok': False, 'message': 'Non conosco l’indirizzo di “%s”.' % label, 'need': 'address'}
    if not (c.get('email') and c.get('password')):
        return {'ok': False, 'message': 'Prima va collegato il tuo account email (Impostazioni → Assistente).', 'need': 'account'}
    msg = EmailMessage()
    msg['From'] = ('%s <%s>' % (c['name'], c['email'])) if c.get('name') else c['email']
    msg['To'] = to
    msg['Subject'] = a.get('oggetto') or a.get('subject') or ''
    msg.set_content(a.get('testo') or a.get('body') or '')
    for p in a.get('allegati') or []:
        p = os.path.expanduser(p)
        if not os.path.isfile(p):
            return {'ok': False, 'message': 'Non trovo l’allegato %s.' % os.path.basename(p)}
        ext = os.path.splitext(p)[1].lower()
        mt = {'.pdf': ('application', 'pdf'),
              '.docx': ('application', 'vnd.openxmlformats-officedocument.wordprocessingml.document'),
              '.xlsx': ('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
              '.jpg': ('image', 'jpeg'), '.jpeg': ('image', 'jpeg'), '.png': ('image', 'png')}.get(ext, ('application', 'octet-stream'))
        msg.add_attachment(open(p, 'rb').read(), maintype=mt[0], subtype=mt[1], filename=os.path.basename(p))
    try:
        _smtp_send(c, msg)
    except smtplib.SMTPAuthenticationError:
        return {'ok': False, 'message': 'Il tuo account email ha rifiutato la password. Con Gmail serve una “password per le app” '
                                        '(Account Google → Sicurezza → Password per le app).', 'need': 'account'}
    except Exception as e:
        return {'ok': False, 'message': 'Email non inviata: %s' % e}
    return {'ok': True, 'to': to}


def mail_test(_=None):
    c = _mail_conf()
    return mail_send({'a': c.get('email'), 'oggetto': 'Prova da Rise',
                      'testo': 'Se leggi questo messaggio, l’Assistente di Rise può inviare email per te.'})


def mail_prepare(a):
    """Bozza dell'email preparata dall'Assistente: indirizzo trovato in rubrica, allegati controllati."""
    to, label = resolve_address(a.get('a') or a.get('destinatario'))
    atts = []
    for p in a.get('allegati') or []:
        p = os.path.expanduser(p)
        if os.path.isfile(p):
            atts.append({'path': p, 'name': os.path.basename(p), 'size': os.path.getsize(p)})
    return {'ok': True, 'to': to, 'label': label, 'oggetto': a.get('oggetto') or '', 'testo': a.get('testo') or '',
            'allegati': atts, 'account': mail_get()}


def mail_thunderbird(a):
    """Senza account configurato: apre Thunderbird con l'email già scritta e l'allegato, l'utente preme Invia."""
    to, _ = resolve_address(a.get('a') or '')
    def q(s):
        return (s or '').replace("'", '’').replace(',', '，')
    parts = ["to='%s'" % (to or ''), "subject='%s'" % q(a.get('oggetto')), "body='%s'" % q(a.get('testo'))]
    atts = [p for p in (a.get('allegati') or []) if os.path.isfile(os.path.expanduser(p))]
    if atts:
        parts.append("attachment='%s'" % ','.join('file://' + os.path.expanduser(p) for p in atts))
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    cmd = ['flatpak', 'run', '--filesystem=home', 'org.mozilla.Thunderbird', '-compose', ','.join(parts)]
    try:
        subprocess.Popen(cmd, start_new_session=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return {'ok': False, 'message': str(e)}
    return {'ok': True}


CMDS = {'auto.mkdir': make_folder, 'auto.find': find_files, 'auto.pdf': make_pdf,
        'contacts.list': contacts, 'contacts.save': contact_save, 'contacts.delete': contact_delete,
        'mail.get': mail_get, 'mail.set': mail_set, 'mail.send': mail_send, 'mail.test': mail_test,
        'mail.prepare': mail_prepare, 'mail.thunderbird': mail_thunderbird}
