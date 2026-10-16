"""Rise – voce umana dell'Assistente con Kokoro (modello da 82 milioni di parametri, licenza Apache 2.0).

Gira come processo a parte (con l'ambiente Python /opt/kokoro) e resta acceso: il modello si carica
una volta sola. Riceve comandi JSON, uno per riga, su stdin:
    {"say": "testo", "voice": "if_sara"}   legge il testo, frase per frase
    {"stop": true}                          zittisce subito
Mentre legge una frase prepara già la successiva, così la voce parte quasi subito.
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import wave

import numpy as np
from kokoro_onnx import Kokoro

DIR = sys.argv[1]
PLAYER = sys.argv[2] if len(sys.argv) > 2 else 'pw-play'
kok = Kokoro(os.path.join(DIR, 'kokoro-v1.0.int8.onnx'), os.path.join(DIR, 'voices-v1.0.bin'))
gen = 0                      # cambia a ogni nuovo comando: le frasi vecchie vengono scartate
play_q = queue.Queue()
cur = {'proc': None}
lock = threading.Lock()


def sentences(t):
    parts = re.split(r'(?<=[.!?;:])\s+|\n+', t)
    out, buf = [], ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        buf = (buf + ' ' + p).strip()
        if len(buf) > 40:            # frasi troppo corte suonano spezzate
            out.append(buf)
            buf = ''
    if buf:
        out.append(buf)
    return out


def player():
    while True:
        g, path = play_q.get()
        if g != gen:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        try:
            p = subprocess.Popen([PLAYER, path], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            cur['proc'] = p
            _, err = p.communicate()
            if p.returncode not in (0, -15) and err:
                print('riproduzione:', err.decode(errors='replace')[-300:], file=sys.stderr, flush=True)
        except OSError as e:
            print('riproduzione:', e, file=sys.stderr, flush=True)
        cur['proc'] = None
        try:
            os.remove(path)
        except OSError:
            pass


def shut():
    p = cur['proc']
    if p and p.poll() is None:
        try:
            p.terminate()
        except OSError:
            pass


def speak(text, voice, g, speed):
    for i, s in enumerate(sentences(text)):
        if g != gen:
            return
        try:
            samples, sr = kok.create(s, voice=voice, speed=speed, lang='it')
        except Exception as e:
            print('errore:', e, file=sys.stderr, flush=True)
            continue
        if g != gen:
            return
        path = '/tmp/rise-voce-%d-%d.wav' % (g, i)
        pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm)
        play_q.put((g, path))


threading.Thread(target=player, daemon=True).start()
print('pronto', flush=True)
for line in sys.stdin:
    try:
        cmd = json.loads(line)
    except ValueError:
        continue
    with lock:
        gen += 1
        g = gen
    shut()
    if cmd.get('say'):
        threading.Thread(target=speak, args=(cmd['say'], cmd.get('voice') or 'if_sara', g,
                                             float(cmd.get('speed') or 1.0)), daemon=True).start()
