"""Rise – installazione sul disco accanto a Windows (interfaccia per rise-install)."""
import json
import os
import subprocess

TOOL = '/usr/local/bin/rise-install'
STATUS = '/tmp/rise-install-status.json'


def _live():
    try:
        return ' boot=casper' in open('/proc/cmdline').read() or os.path.isdir('/cdrom/casper')
    except OSError:
        return False


def scan(_=None):
    if not _live():
        return {'ok': False, 'message': 'Rise è già installato su questo computer.'}
    try:
        r = subprocess.run(['sudo', '-n', 'python3', TOOL, 'scan'], capture_output=True, text=True, timeout=60)
        with open('/tmp/rise-install.log', 'a') as f:
            f.write(r.stderr)
        res = json.loads(r.stdout or '{}')
        if not res:
            return {'ok': False, 'message': 'Non riesco a leggere i dischi: %s' % (r.stderr.strip().splitlines() or ['errore sconosciuto'])[-1][-200:]}
    except Exception as e:
        return {'ok': False, 'message': 'Non riesco a leggere i dischi: %s' % e}
    # file dell'Assistente già scaricati (da copiare per non riscaricarli)
    extra, total = [], 0
    try:
        import risestore
        for sub in ('models', 'voce'):
            d = risestore.data_dir(sub)[0]
            size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(d) for f in fs if not f.endswith('.part'))
            if size > 1 << 20:
                extra.append(d)
                total += size
    except Exception:
        pass
    res.update(models=extra, modelsBytes=total)
    return res


def start(a):
    if not _live():
        return {'ok': False, 'message': 'Rise è già installato.'}
    try:
        st = json.load(open(STATUS))
        if not st.get('done') and st.get('phase') not in (None, 'error'):
            return {'ok': True, 'already': True}
    except (OSError, ValueError):
        pass
    try:
        os.remove(STATUS)
    except OSError:
        pass
    p = subprocess.Popen(['sudo', '-n', 'python3', TOOL, 'run'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=open('/tmp/rise-install.log', 'a'), start_new_session=True, text=True)
    p.stdin.write(json.dumps(a))
    p.stdin.close()
    return {'ok': True}


def status(_=None):
    try:
        return {'ok': True, 'status': json.load(open(STATUS))}
    except (OSError, ValueError):
        return {'ok': True, 'status': {}}


CMDS = {'install.scan': scan, 'install.start': start, 'install.status': status}
