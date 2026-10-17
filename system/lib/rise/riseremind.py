"""Rise – promemoria e timer dell'Assistente ("ricordami alle 17 di chiamare Rossi", "timer di 10 minuti").

I promemoria stanno in ~/.config/rise/promemoria.json; un controllo ogni 15 secondi (dentro rise-shell)
li fa suonare: finestra in primo piano più la voce dell'Assistente.
"""
import datetime as dt
import json
import os
import re
import threading
import time
import uuid

FILE = os.path.join(os.path.expanduser('~'), '.config', 'rise', 'promemoria.json')
_lock = threading.Lock()
GIORNI = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']
NUM = {'un': 1, 'uno': 1, 'una': 1, 'due': 2, 'tre': 3, 'quattro': 4, 'cinque': 5, 'sei': 6, 'sette': 7, 'otto': 8,
       'nove': 9, 'dieci': 10, 'quindici': 15, 'venti': 20, 'trenta': 30, 'quaranta': 40, 'mezz': 30, 'mezza': 30,
       'quarantacinque': 45, 'un quarto': 15}


def _load():
    try:
        return json.load(open(FILE))
    except Exception:
        return []


def _save(items):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + '.tmp'
    json.dump(items, open(tmp, 'w'), ensure_ascii=False)
    os.replace(tmp, FILE)


def _n(s):
    s = (s or '').strip().lower()
    if s.isdigit():
        return int(s)
    return NUM.get(s)


def parse_when(text, now=None):
    """'alle 17', 'alle 17:30', 'domani alle 9', 'lunedì alle 15', 'tra 10 minuti', 'tra un'ora', 'stasera'…"""
    now = now or dt.datetime.now()
    t = (text or '').lower().replace('’', "'")
    t = t.replace('ventiquattro', '24')
    # tra / fra N minuti / ore
    m = re.search(r"\b(?:tra|fra|entro)\s+(?:(\d+|un|uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|quindici|venti|trenta|quaranta|quarantacinque|mezz)'?\s*(?:a\s+)?)?(minut\w*|or[ae]\b|secondi)", t)
    if m:
        n = _n(m.group(1) or 'un') or 1
        if m.group(1) == 'mezz':
            return now + dt.timedelta(minutes=30)
        unit = m.group(2)
        if unit.startswith('minut'):
            return now + dt.timedelta(minutes=n)
        if unit.startswith('second'):
            return now + dt.timedelta(seconds=n)
        return now + dt.timedelta(hours=n)
    m = re.search(r"\b(?:timer|sveglia)\s+(?:di\s+|da\s+)?(\d+|un|uno|una|due|tre|cinque|dieci|quindici|venti|trenta)\s*(minut\w*|or[ae]\b|secondi)", t)
    if m:
        n = _n(m.group(1)) or 1
        return now + (dt.timedelta(minutes=n) if m.group(2).startswith('minut') else
                      dt.timedelta(seconds=n) if m.group(2).startswith('second') else dt.timedelta(hours=n))
    # giorno
    day = now.date()
    if 'dopodomani' in t:
        day = day + dt.timedelta(days=2)
    elif 'domani' in t:
        day = day + dt.timedelta(days=1)
    else:
        for i, g in enumerate(GIORNI):
            if re.search(r'\b%s\b' % g.replace('ì', 'i'), t.replace('ì', 'i')):
                delta = (i - now.weekday()) % 7
                day = day + dt.timedelta(days=delta)
                break
        m = re.search(r'\b(?:il\s+)?(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b', t)
        if m:
            y = int(m.group(3)) if m.group(3) else now.year
            y = y + 2000 if y < 100 else y
            try:
                day = dt.date(y, int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
    # ora
    hh = mm = None
    m = re.search(r"\b(?:alle|all'|ore|per le|verso le|a)\s*(\d{1,2})(?:[:.,](\d{2})|\s+e\s+(\d{1,2}|mezza|mezzo|un quarto|quarantacinque|trenta|quindici))?\b", t)
    if m:
        hh = int(m.group(1))
        if m.group(2):
            mm = int(m.group(2))
        elif m.group(3):
            mm = {'mezza': 30, 'mezzo': 30, 'un quarto': 15}.get(m.group(3)) or _n(m.group(3)) or 0
        else:
            mm = 0
        if re.search(r'\b(di sera|stasera|del pomeriggio|pomeriggio)\b', t) and hh < 12:
            hh += 12
    elif 'mezzogiorno' in t:
        hh, mm = 12, 0
    elif 'stasera' in t:
        hh, mm = 20, 0
    elif 'stamattina' in t or 'domattina' in t or 'mattina' in t:
        hh, mm = 9, 0
    elif 'pomeriggio' in t:
        hh, mm = 15, 0
    if hh is None:
        if day != now.date():
            hh, mm = 9, 0
        else:
            return None
    if hh > 23 or mm > 59:
        return None
    when = dt.datetime.combine(day, dt.time(hh, mm))
    # "alle 5" detto alle 14 = 17; un orario già passato oggi = domani
    if when <= now and day == now.date():
        if hh < 12 and when + dt.timedelta(hours=12) > now:
            when += dt.timedelta(hours=12)
        else:
            when += dt.timedelta(days=1)
    return when


def _fmt(when, now=None):
    now = now or dt.datetime.now()
    if when.date() == now.date():
        d = 'oggi'
    elif when.date() == now.date() + dt.timedelta(days=1):
        d = 'domani'
    else:
        d = '%s %d/%d' % (GIORNI[when.weekday()], when.day, when.month)
    return '%s alle %02d:%02d' % (d, when.hour, when.minute)


def add(a):
    text = (a.get('testo') or a.get('text') or '').strip() or 'Promemoria'
    when = None
    if a.get('iso'):
        try:
            when = dt.datetime.fromisoformat(a['iso'])
        except ValueError:
            when = None
    when = when or parse_when(a.get('quando') or a.get('when') or text)
    if not when:
        return {'ok': False, 'message': 'A che ora te lo ricordo?'}
    text = re.sub(r"^(di|che|per)\s+", '', text, flags=re.I)
    r = {'id': uuid.uuid4().hex[:8], 'text': text[:200], 'at': when.isoformat(timespec='seconds'),
         'timer': bool(a.get('timer'))}
    with _lock:
        items = _load()
        items.append(r)
        _save(sorted(items, key=lambda x: x['at']))
    return {'ok': True, 'id': r['id'], 'text': r['text'], 'at': r['at'], 'label': _fmt(when)}


def lst(_=None):
    now = dt.datetime.now()
    out = []
    for r in _load():
        w = dt.datetime.fromisoformat(r['at'])
        out.append(dict(r, label=_fmt(w, now)))
    return {'ok': True, 'items': out}


def delete(a):
    with _lock:
        items = _load()
        key = (a.get('id') or '').strip()
        txt = (a.get('testo') or '').lower().strip()
        keep = [r for r in items if not (r['id'] == key or (txt and txt in r['text'].lower()) or a.get('tutti'))]
        _save(keep)
    return {'ok': True, 'removed': len(items) - len(keep)}


def snooze(a):
    minutes = int(a.get('minuti') or 10)
    r = {'testo': a.get('testo') or 'Promemoria', 'iso': (dt.datetime.now() + dt.timedelta(minutes=minutes)).isoformat(timespec='seconds')}
    return add(r)


def _loop(cb):
    while True:
        try:
            now = dt.datetime.now()
            with _lock:
                items = _load()
                due = [r for r in items if dt.datetime.fromisoformat(r['at']) <= now]
                if due:
                    _save([r for r in items if r not in due])
            for r in due:
                cb(r)
        except Exception:
            pass
        time.sleep(15)


def start(cb):
    threading.Thread(target=_loop, args=(cb,), daemon=True).start()


CMDS = {'remind.add': add, 'remind.list': lst, 'remind.delete': delete, 'remind.snooze': snooze}
