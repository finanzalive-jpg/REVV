"""Rise – parola di attivazione dell'Assistente ("Ehi Sara", "Ehi Nicola").

Gira come processo a parte, con l'ambiente /opt/rise-py (vosk, licenza Apache 2.0) e il piccolo modello
italiano di Vosk. Ascolta sempre il microfono, ma riconosce solo poche frasi: "ehi <nome>", "ciao <nome>",
"ok <nome>". Non registra nulla e non manda nulla a nessuno.

    argomenti: <cartella del modello> <json {"f": "sara", "m": "nicola"}>
    stdin:     {"pause": true} / {"pause": false}       (mentre l'Assistente parla o ascolta la domanda)
    stdout:    pronto
               {"wake": "f"}                             quando sente il nome
"""
import json
import os
import select
import shutil
import subprocess
import sys
import time
import unicodedata

from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)
RATE = 16000
PREFIX = ('ehi', 'ciao', 'ok', 'hey', 'senti')


def norm(s):
    s = unicodedata.normalize('NFKD', s.lower())
    return ''.join(c for c in s if c.isalpha() or c == ' ').strip()


def main():
    model = Model(sys.argv[1])
    names = {k: norm(v) for k, v in json.loads(sys.argv[2]).items() if norm(v)}
    phrases = sorted({'%s %s' % (p, n) for n in names.values() for p in PREFIX})
    rec = KaldiRecognizer(model, RATE, json.dumps(phrases + ['[unk]'], ensure_ascii=False))
    if shutil.which('arecord'):
        cmd = ['arecord', '-q', '-f', 'S16_LE', '-r', str(RATE), '-c', '1', '-t', 'raw']
    else:
        cmd = ['pw-record', '--rate', str(RATE), '--channels', '1', '--format', 's16', '-']
    mic = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    print('pronto', flush=True)
    paused, last = False, 0.0
    fd = mic.stdout.fileno()
    while True:
        # comandi dall'Assistente (pausa / ripresa), senza bloccare l'ascolto
        r, _, _ = select.select([sys.stdin, fd], [], [], 1.0)
        if sys.stdin in r:
            line = sys.stdin.readline()
            if not line:
                break
            try:
                c = json.loads(line)
            except ValueError:
                c = {}
            if 'pause' in c and bool(c['pause']) != paused:
                paused = bool(c['pause'])
                rec.Reset()
        if fd not in r:
            if mic.poll() is not None:
                print('microfono chiuso', file=sys.stderr, flush=True)
                time.sleep(3)
                mic = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                fd = mic.stdout.fileno()
            continue
        data = os.read(fd, 3200)                       # 0,1 secondi
        if not data:
            continue
        if paused:
            continue
        if rec.AcceptWaveform(data):
            text = json.loads(rec.Result()).get('text', '')
        else:
            text = json.loads(rec.PartialResult()).get('partial', '')
        if not text or time.time() - last < 2.5:
            continue
        who = heard(norm(text).split(), names)
        if who:
            last = time.time()
            rec.Reset()
            print(json.dumps({'wake': who}), flush=True)


def heard(words, names):
    for i, w in enumerate(words[:-1]):
        if w in PREFIX:
            for who, n in names.items():
                if ' '.join(words[i + 1:i + 1 + len(n.split())]) == n:
                    return who
    return None

if __name__ == '__main__':
    main()
