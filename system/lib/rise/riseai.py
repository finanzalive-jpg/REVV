"""Rise – Assistente: intelligenza artificiale che gira sul computer, senza internet.

Motore: llama.cpp (llama-server, dal pacchetto Ubuntu llama.cpp-tools, licenza MIT).
Modelli: Qwen3.5 (Alibaba, licenza Apache 2.0) in formato GGUF compresso a 4 bit, scaricati
da Hugging Face (unsloth/Qwen3.5-*-GGUF) solo quando l'utente lo chiede.

Il desktop parla direttamente con llama-server su http://127.0.0.1:8737 (API compatibile OpenAI).
"""
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.request

HOME = os.path.expanduser('~')
MODEL_DIR = os.path.join(HOME, '.local', 'share', 'rise', 'models')
PORT = 8737
HF = 'https://huggingface.co/{repo}/resolve/main/{file}'

# id: (nome mostrato, repository, file, dimensione indicativa in GB, RAM minima consigliata in GB)
MODELS = {
    '0.8b': ('Qwen3.5 0.8B · leggerissimo', 'unsloth/Qwen3.5-0.8B-GGUF', 'Qwen3.5-0.8B-Q4_K_M.gguf', 0.6, 3),
    '2b':   ('Qwen3.5 2B · equilibrato', 'unsloth/Qwen3.5-2B-GGUF', 'Qwen3.5-2B-Q4_K_M.gguf', 1.3, 5),
    '4b':   ('Qwen3.5 4B · più bravo', 'unsloth/Qwen3.5-4B-GGUF', 'Qwen3.5-4B-Q4_K_M.gguf', 2.7, 8),
    '9b':   ('Qwen3.5 9B · il migliore', 'unsloth/Qwen3.5-9B-GGUF', 'Qwen3.5-9B-Q4_K_M.gguf', 5.7, 14),
}

_dl = {}          # id -> {'done', 'total', 'error', 'running', 'cancel'}
_srv = {'proc': None, 'model': None}
_lock = threading.Lock()


def _mem_gb():
    try:
        for l in open('/proc/meminfo'):
            if l.startswith('MemTotal:'):
                return int(l.split()[1]) / 1048576
    except OSError:
        pass
    return 4


def _live():
    return os.path.exists('/run/casper') or os.path.exists('/cdrom/casper')


def _path(mid):
    return os.path.join(MODEL_DIR, MODELS[mid][2])


def _engine():
    for name in ('llama-server', '/usr/libexec/llama.cpp/llama-server', '/usr/lib/llama.cpp/llama-server'):
        p = shutil.which(name) or (name if os.path.isfile(name) and os.access(name, os.X_OK) else None)
        if p:
            return p
    return None


def recommended():
    """Il modello giusto per questo computer (dalla chiavetta i file stanno in memoria: si sta più bassi)."""
    ram = _mem_gb() - (1.5 if _live() else 0)
    best = '0.8b'
    for mid in ('0.8b', '2b', '4b', '9b'):
        if ram >= MODELS[mid][4]:
            best = mid
    return best


def status(_=None):
    models = []
    for mid, (name, repo, f, gb, ram) in MODELS.items():
        p = _path(mid)
        d = _dl.get(mid) or {}
        models.append({'id': mid, 'name': name, 'gb': gb, 'ram': ram,
                       'have': os.path.exists(p), 'downloading': bool(d.get('running')),
                       'done': d.get('done', 0), 'total': d.get('total', 0), 'error': d.get('error')})
    proc = _srv['proc']
    running = bool(proc and proc.poll() is None)
    return {'engine': bool(_engine()), 'models': models, 'recommended': recommended(),
            'ramGb': round(_mem_gb(), 1), 'live': _live(), 'running': running,
            'model': _srv['model'] if running else None, 'port': PORT}


# ---------- scaricamento ----------
def _download(mid):
    name, repo, f, gb, ram = MODELS[mid]
    os.makedirs(MODEL_DIR, exist_ok=True)
    dst, part = _path(mid), _path(mid) + '.part'
    st = _dl[mid]
    try:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        req = urllib.request.Request(HF.format(repo=repo, file=f), headers={'User-Agent': 'Rise/0.5'})
        if have:
            req.add_header('Range', 'bytes=%d-' % have)
        with urllib.request.urlopen(req, timeout=30) as r:
            if have and r.status != 206:      # il server non riprende: si ricomincia
                have = 0
            total = int(r.headers.get('Content-Length') or 0) + have
            st.update(done=have, total=total)
            with open(part, 'ab' if have else 'wb') as out:
                while True:
                    if st.get('cancel'):
                        raise InterruptedError('annullato')
                    buf = r.read(1 << 20)
                    if not buf:
                        break
                    out.write(buf)
                    st['done'] += len(buf)
        if st['total'] and st['done'] < st['total']:
            raise IOError('scaricamento interrotto')
        os.replace(part, dst)
    except InterruptedError:
        st['error'] = None
        try:
            os.remove(part)
        except OSError:
            pass
    except Exception as e:
        st['error'] = 'Non riesco a scaricare il modello. Controlla la connessione e riprova. (%s)' % e
    finally:
        st['running'] = False


def download(a):
    mid = a.get('model')
    if mid not in MODELS:
        return {'ok': False, 'message': 'Modello sconosciuto.'}
    with _lock:
        if (_dl.get(mid) or {}).get('running'):
            return {'ok': True}
        free = shutil.disk_usage(os.path.dirname(MODEL_DIR) if os.path.isdir(os.path.dirname(MODEL_DIR)) else HOME).free / 1e9
        if free < MODELS[mid][3] + 0.3:
            return {'ok': False, 'message': 'Non c’è abbastanza spazio (servono circa %.1f GB).' % MODELS[mid][3]}
        _dl[mid] = {'running': True, 'done': 0, 'total': 0, 'error': None, 'cancel': False}
    threading.Thread(target=_download, args=(mid,), daemon=True).start()
    return {'ok': True}


def cancel(a):
    d = _dl.get(a.get('model'))
    if d:
        d['cancel'] = True
    return {'ok': True}


def delete(a):
    mid = a.get('model')
    if mid not in MODELS:
        return {'ok': False}
    if _srv['model'] == mid:
        stop()
    for p in (_path(mid), _path(mid) + '.part'):
        try:
            os.remove(p)
        except OSError:
            pass
    return {'ok': True}


# ---------- motore ----------
def _healthy(timeout=1.0):
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/health' % PORT, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start(a):
    mid = a.get('model') or recommended()
    if mid not in MODELS or not os.path.exists(_path(mid)):
        return {'ok': False, 'message': 'Il modello non è ancora stato scaricato.'}
    eng = _engine()
    if not eng:
        return {'ok': False, 'message': 'Il motore dell’Assistente (llama.cpp) non è installato.'}
    with _lock:
        p = _srv['proc']
        if p and p.poll() is None and _srv['model'] == mid:
            return {'ok': True, 'port': PORT}
        stop()
        threads = max(1, min(8, (os.cpu_count() or 2)))
        cmd = [eng, '-m', _path(mid), '--host', '127.0.0.1', '--port', str(PORT),
               '-c', '8192' if mid in ('4b', '9b') else '4096', '-t', str(threads), '--jinja',
               '--temp', '0.7', '--top-p', '0.8', '--top-k', '20', '--min-p', '0',
               '--presence-penalty', '1.5', '--no-webui']
        log = open('/tmp/rise-assistente.log', 'w')
        env = dict(os.environ)
        env.pop('LD_PRELOAD', None)
        _srv['proc'] = subprocess.Popen(cmd, stdout=log, stderr=log, env=env, start_new_session=True,
                                        preexec_fn=lambda: os.nice(5))
        _srv['model'] = mid
    for _ in range(240):                     # caricare il modello può richiedere qualche decina di secondi
        if _srv['proc'].poll() is not None:
            tail = ''
            try:
                tail = open('/tmp/rise-assistente.log').read()[-300:]
            except OSError:
                pass
            if 'unknown argument' in tail or 'invalid argument' in tail:
                return _start_plain(eng, mid)
            last = tail.strip().splitlines()[-1] if tail.strip() else ''
            return {'ok': False, 'message': ('L’Assistente non è partito. ' + last).strip()}
        if _healthy():
            return {'ok': True, 'port': PORT}
        time.sleep(0.5)
    return {'ok': False, 'message': 'L’Assistente ci mette troppo a partire.'}


def _start_plain(eng, mid):
    """Versioni di llama-server che non conoscono qualche opzione: si riparte con quelle essenziali."""
    cmd = [eng, '-m', _path(mid), '--host', '127.0.0.1', '--port', str(PORT), '-c', '4096', '--jinja']
    log = open('/tmp/rise-assistente.log', 'a')
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    _srv['proc'] = subprocess.Popen(cmd, stdout=log, stderr=log, env=env, start_new_session=True)
    for _ in range(240):
        if _srv['proc'].poll() is not None:
            return {'ok': False, 'message': 'L’Assistente non è partito.'}
        if _healthy():
            return {'ok': True, 'port': PORT}
        time.sleep(0.5)
    return {'ok': False, 'message': 'L’Assistente ci mette troppo a partire.'}


def stop(_=None):
    p = _srv['proc']
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    _srv['proc'] = None
    _srv['model'] = None
    return {'ok': True}


CMDS = {'ai.status': status, 'ai.download': download, 'ai.cancel': cancel, 'ai.delete': delete,
        'ai.start': start, 'ai.stop': stop}
