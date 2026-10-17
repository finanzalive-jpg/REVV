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
import time
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


def envelope(samples, sr, ms=20):
    """Apertura della bocca dell'avatar, a passi di 20 ms: segue le sillabe (picchi di energia delle
    vocali rispetto alle sillabe vicine), così la bocca si apre e si chiude a tempo con le parole."""
    x = np.asarray(samples, dtype=np.float32)
    n = max(1, int(sr * ms / 1000))
    k = len(x) // n
    if k == 0:
        return []
    fr = x[:k * n].reshape(k, n)
    spec = np.abs(np.fft.rfft(fr * np.hanning(n), axis=1))
    f = np.fft.rfftfreq(n, 1.0 / sr)
    e = spec[:, (f > 250) & (f < 3000)].sum(1)            # banda delle vocali
    e = np.convolve(e, np.ones(2) / 2, 'same')
    e = e / (float(np.percentile(e, 97)) or 1.0)
    w = 6                                                  # ±120 ms, circa una sillaba
    lo = np.array([e[max(0, i - w):i + w + 1].min() for i in range(k)])
    hi = np.array([e[max(0, i - w):i + w + 1].max() for i in range(k)])
    o = (e - lo) / np.maximum(hi - lo, 0.08)
    o = o * np.clip(e * 3, 0, 1)                          # in silenzio resta chiusa
    o = np.clip(o, 0, 1) ** 1.2 * np.clip(hi * 1.3, 0, 1)
    return [round(float(v), 2) for v in o]


def player():
    while True:
        g, path, env = play_q.get()
        if g != gen:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        try:
            p = subprocess.Popen([PLAYER, path], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            cur['proc'] = p
            # apertura della bocca per questa frase, a passi di 20 ms: l'avatar la segue
            print(json.dumps({'env': env, 'step': 20}), flush=True)
            _, err = p.communicate()
            if p.returncode not in (0, -15) and err:
                print('riproduzione:', err.decode(errors='replace')[-300:], file=sys.stderr, flush=True)
        except OSError as e:
            print('riproduzione:', e, file=sys.stderr, flush=True)
        cur['proc'] = None
        if play_q.empty():
            print('{"end": 1}', flush=True)
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
        path = '/tmp/rise-voce-%d-%d-%d.wav' % (g, i, int(time.time() * 1000) % 100000)
        pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm)
        play_q.put((g, path, envelope(samples, sr)))


synth_q = queue.Queue()


def synth():
    while True:
        g, text, voice, speed = synth_q.get()
        if g == gen:
            speak(text, voice, g, speed)


threading.Thread(target=player, daemon=True).start()
threading.Thread(target=synth, daemon=True).start()
print('pronto', flush=True)
for line in sys.stdin:
    try:
        cmd = json.loads(line)
    except ValueError:
        continue
    if cmd.get('append') and cmd.get('say'):
        # pezzo successivo della stessa risposta: si mette in coda, senza interrompere
        synth_q.put((gen, cmd['say'], cmd.get('voice') or 'if_sara', float(cmd.get('speed') or 1.0)))
        continue
    with lock:
        gen += 1
        g = gen
    shut()
    if cmd.get('say'):
        synth_q.put((g, cmd['say'], cmd.get('voice') or 'if_sara', float(cmd.get('speed') or 1.0)))
