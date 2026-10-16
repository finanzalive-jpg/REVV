"""Rise – voce dell'Assistente: ascolta dal microfono e risponde a voce, tutto sul computer.

Ascolto: whisper.cpp (whisper-cli, pacchetto Ubuntu whisper.cpp, licenza MIT) con il modello
         multilingue di OpenAI in formato ggml (scaricato da Hugging Face la prima volta).
Voce:    Kokoro (82M, Apache 2.0) con le voci italiane Sara (femminile) e Nicola (maschile), molto
         naturali; il modello si scarica la prima volta. Se Kokoro manca si usa Piper (MIT).
Registrazione e riproduzione: PipeWire (pw-record / pw-play), con ALSA come riserva.
"""
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request

import risestore

HOME = os.path.expanduser('~')
DIR = risestore.data_dir('voce')[0]      # dalla chiavetta: salvata sulla chiavetta, si scarica una volta sola
HF = 'https://huggingface.co/'
WAV_IN = '/tmp/rise-microfono.wav'
WAV_OUT = '/tmp/rise-voce.wav'

STT = {
    'base': ('ggml-base-q5_1.bin', HF + 'ggerganov/whisper.cpp/resolve/main/ggml-base-q5_1.bin', 60),
    'small': ('ggml-small-q5_1.bin', HF + 'ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin', 190),
}
_PV = HF + 'rhasspy/piper-voices/resolve/main/it/it_IT/'
VOICES = {
    'f': ('Femminile (Paola)', 'it_IT-paola-medium.onnx', _PV + 'paola/medium/it_IT-paola-medium.onnx', 63),
    'm': ('Maschile (Riccardo)', 'it_IT-riccardo-x_low.onnx', _PV + 'riccardo/x_low/it_IT-riccardo-x_low.onnx', 28),
}

KOKORO_PY = '/opt/kokoro/bin/python'
KOKORO_REL = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'
KOKORO_FILES = (('kokoro-v1.0.int8.onnx', 92), ('voices-v1.0.bin', 28))
KOKORO_VOICES = {'f': 'if_sara', 'm': 'im_nicola'}
KOKORO_NAMES = {'f': 'Femminile (Sara)', 'm': 'Maschile (Nicola)'}
_tts = {'proc': None}

_dl = {'running': False, 'done': 0, 'total': 0, 'error': None, 'what': ''}
_rec = {'proc': None, 'start': 0}
_play = {'proc': None}
_lock = threading.Lock()


def _mem_gb():
    try:
        for l in open('/proc/meminfo'):
            if l.startswith('MemTotal:'):
                return int(l.split()[1]) / 1048576
    except OSError:
        pass
    return 4


def _stt_id():
    # sui computer con più memoria e processore si usa il modello più preciso
    return 'small' if _mem_gb() >= 7.5 and (os.cpu_count() or 2) >= 8 else 'base'


def _which(*names):
    for n in names:
        p = shutil.which(n) or (n if os.path.isfile(n) and os.access(n, os.X_OK) else None)
        if p:
            return p
    return None


def _whisper():
    return _which('whisper-cli', 'whisper-cpp', '/usr/local/bin/whisper-cli')


def _piper():
    return _which('piper-tts', '/opt/piper/piper', '/usr/local/bin/piper-tts')


def _have_stt():
    return os.path.exists(os.path.join(DIR, STT[_stt_id()][0]))


def _kokoro():
    return os.access(KOKORO_PY, os.X_OK)


def _have_voice(v):
    if _kokoro():
        return all(os.path.exists(os.path.join(DIR, f)) for f, _ in KOKORO_FILES)
    f = VOICES[v][1]
    return os.path.exists(os.path.join(DIR, f)) and os.path.exists(os.path.join(DIR, f + '.json'))


def status(_=None):
    kk = _kokoro()
    return {'ok': True, 'listen': bool(_whisper()), 'speak': kk or bool(_piper()), 'engine': 'kokoro' if kk else 'piper',
            'sttReady': _have_stt(),
            'voices': {k: {'name': KOKORO_NAMES[k] if kk else v[0], 'ready': _have_voice(k),
                           'mb': sum(m for f, m in KOKORO_FILES if not os.path.exists(os.path.join(DIR, f))) if kk else v[3]}
                       for k, v in VOICES.items()},
            'sttMb': STT[_stt_id()][2], 'download': dict(_dl), 'recording': bool(_rec['proc'] and _rec['proc'].poll() is None)}


# ---------- scaricamento dei modelli ----------
def _fetch(url, dst):
    part = dst + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': 'Rise/0.5'})
    with urllib.request.urlopen(req, timeout=30) as r, risestore.open_write(part) as out:
        _dl['total'] += int(r.headers.get('Content-Length') or 0)
        while True:
            buf = r.read(1 << 18)
            if not buf:
                break
            out.write(buf)
            _dl['done'] += len(buf)
    risestore.replace(part, dst)


def _setup(voice):
    try:
        jobs = []
        if not _have_stt():
            f, url, _ = STT[_stt_id()]
            jobs.append((url, f))
        if voice in VOICES and not _have_voice(voice):
            if _kokoro():
                jobs += [(KOKORO_REL + f, f) for f, _ in KOKORO_FILES if not os.path.exists(os.path.join(DIR, f))]
            else:
                _, f, url, _ = VOICES[voice]
                jobs += [(url, f), (url + '.json', f + '.json')]
        for url, f in jobs:
            _dl['what'] = f
            _fetch(url, os.path.join(DIR, f))
        risestore.sync()
    except Exception as e:
        _dl['error'] = 'Non riesco a scaricare la voce. Controlla la connessione e riprova. (%s)' % e
    finally:
        _dl['running'] = False


def setup(a):
    with _lock:
        if _dl['running']:
            return {'ok': True}
        _dl.update(running=True, done=0, total=0, error=None, what='')
    threading.Thread(target=_setup, args=(a.get('voice') or 'f',), daemon=True).start()
    return {'ok': True}


# ---------- ascolto ----------
def listen_start(_=None):
    if not _whisper():
        return {'ok': False, 'message': 'Il riconoscimento della voce (whisper.cpp) non è installato.'}
    if not _have_stt():
        return {'ok': False, 'need': 'download'}
    stop_speaking()
    listen_cancel()
    try:
        os.remove(WAV_IN)
    except OSError:
        pass
    rec = _which('pw-record')
    if rec:
        cmd = [rec, '--rate', '16000', '--channels', '1', '--format', 's16', WAV_IN]
    elif _which('arecord'):
        cmd = ['arecord', '-q', '-f', 'S16_LE', '-r', '16000', '-c', '1', WAV_IN]
    else:
        return {'ok': False, 'message': 'Non trovo il programma per registrare dal microfono.'}
    _rec['proc'] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    _rec['start'] = time.time()
    return {'ok': True}


def _stop_rec():
    p = _rec['proc']
    _rec['proc'] = None
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGINT)
            p.wait(3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def listen_cancel(_=None):
    _stop_rec()
    return {'ok': True}


def listen_stop(_=None):
    long = time.time() - _rec['start']
    _stop_rec()
    if long < 0.6 or not os.path.exists(WAV_IN) or os.path.getsize(WAV_IN) < 8000:
        return {'ok': False, 'message': 'Non ho sentito niente. Controlla che il microfono sia acceso e riprova.'}
    threads = str(max(1, min(8, (os.cpu_count() or 2))))
    cmd = [_whisper(), '-m', os.path.join(DIR, STT[_stt_id()][0]), '-f', WAV_IN, '-l', 'it',
           '-t', threads, '-nt', '-np']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'message': 'Ci ho messo troppo a capire. Riprova con una frase più corta.'}
    text = ' '.join(l.strip() for l in r.stdout.splitlines() if l.strip())
    text = re.sub(r'\[[^\]]*\]|\([^)]*\)', '', text).strip()      # [musica], (rumore)…
    if not text:
        return {'ok': False, 'message': 'Non ho capito. Riprova parlando un po’ più vicino al microfono.'}
    return {'ok': True, 'text': text}


# ---------- voce ----------
def _clean_for_voice(t):
    t = re.sub(r'```[\s\S]*?```', ' ', t)
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)           # link markdown → solo il testo
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'^\s*\|.*\|\s*$', '', t, flags=re.M)          # tabelle
    t = re.sub(r'[#*_`>|]', '', t)
    t = re.sub(r'^\s*[-•]\s+', '', t, flags=re.M)
    t = t.replace('€', ' euro').replace('%', ' per cento')
    return re.sub(r'\s+\n', '\n', t).strip()[:1500]


def _tts_proc():
    p = _tts['proc']
    if p and p.poll() is None:
        return p
    player = _which('pw-play') or _which('aplay') or 'true'
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    env['OMP_NUM_THREADS'] = str(max(1, min(4, os.cpu_count() or 2)))
    p = subprocess.Popen(['nice', '-n', '2', KOKORO_PY, '-I', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'risetts.py'), DIR, player],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=open('/tmp/rise-voce.log', 'a'),
                         env=env, start_new_session=True, text=True)
    _tts['proc'] = p
    return p


def _tts_send(obj):
    import json
    try:
        p = _tts_proc()
        p.stdin.write(json.dumps(obj) + '\n')
        p.stdin.flush()
        return True
    except (OSError, ValueError):
        _tts['proc'] = None
        return False


def stop_speaking(_=None):
    if _tts['proc'] and _tts['proc'].poll() is None:
        _tts_send({'stop': True})
    p = _play['proc']
    _play['proc'] = None
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except Exception:
            pass
    return {'ok': True}


def say(a):
    v = a.get('voice') or 'f'
    if _kokoro() and v in KOKORO_VOICES:
        if not _have_voice(v):
            return {'ok': False, 'need': 'download'}
        text = _clean_for_voice(a.get('text') or '')
        if text:
            _tts_send({'say': text, 'voice': KOKORO_VOICES[v], 'speed': a.get('speed') or 1.0})
        return {'ok': True}
    exe = _piper()
    if v not in VOICES or not exe:
        return {'ok': False, 'message': 'La voce non è installata.'}
    if not _have_voice(v):
        return {'ok': False, 'need': 'download'}
    text = _clean_for_voice(a.get('text') or '')
    if not text:
        return {'ok': True}
    stop_speaking()
    model = os.path.join(DIR, VOICES[v][1])
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    if exe.startswith('/opt/piper'):
        env['LD_LIBRARY_PATH'] = '/opt/piper'
    player = _which('pw-play') or _which('aplay')
    speed = {'f': '1.0', 'm': '1.0'}[v]
    # piper scrive il file audio, poi lo si fa ascoltare; tutto in un unico gruppo, così si può fermare
    script = '"$0" --model "$1" --length_scale "$2" --output_file "$3" && exec "$4" "$3"'
    extra = []
    if exe.startswith('/opt/piper') and os.path.isdir('/opt/piper/espeak-ng-data'):
        script = '"$0" --model "$1" --length_scale "$2" --espeak_data /opt/piper/espeak-ng-data --output_file "$3" && exec "$4" "$3"'
    p = subprocess.Popen(['sh', '-c', script, exe, model, speed, WAV_OUT, player or 'true'] + extra,
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=open('/tmp/rise-voce.log', 'w'),
                         env=env, start_new_session=True)
    try:
        p.stdin.write(text.encode())
        p.stdin.close()
    except OSError:
        pass
    _play['proc'] = p
    return {'ok': True}


def warm(_=None):
    """Accende in anticipo il motore della voce (caricare il modello richiede qualche secondo)."""
    if _kokoro() and all(os.path.exists(os.path.join(DIR, f)) for f, _ in KOKORO_FILES):
        _tts_proc()
    return {'ok': True}


def shutdown(_=None):
    stop_speaking()
    p = _tts['proc']
    _tts['proc'] = None
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except Exception:
            pass
    return {'ok': True}


def speaking(_=None):
    p = _play['proc']
    return {'ok': True, 'speaking': bool(p and p.poll() is None)}


CMDS = {'voice.status': status, 'voice.setup': setup, 'voice.listenStart': listen_start,
        'voice.listenStop': listen_stop, 'voice.listenCancel': listen_cancel, 'voice.say': say,
        'voice.stop': stop_speaking, 'voice.speaking': speaking, 'voice.warm': warm, 'voice.shutdown': shutdown}
