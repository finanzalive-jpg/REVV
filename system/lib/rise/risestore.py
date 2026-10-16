"""Rise – dove tenere i file grandi (modelli dell'Assistente, voci).

Rise installato: nella cartella dell'utente (~/.local/share/rise/...).
Rise dalla chiavetta: la memoria del computer si svuota a ogni spegnimento, quindi i modelli
si salvano sulla chiavetta stessa, nella cartella "rise-dati". Così si scaricano una volta sola.
  - chiavetta preparata con Rufus (FAT32/exFAT/NTFS): la si rende scrivibile e si scrive come root;
  - chiavetta scritta "a immagine" (sola lettura): si usa la partizione "writable"/"casper-rw" se c'è;
  - altrimenti si resta nella memoria del computer, come prima.
"""
import os
import pwd
import subprocess

HOME = os.path.expanduser('~')
MEDIUM = '/cdrom'
FAT_LIMIT = 4 * 1024 ** 3 - 1          # FAT32 non accetta file da 4 GB in su
_cache = {}


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except Exception:
        return 1, ''


def _mount_of(path):
    best = None
    try:
        for l in open('/proc/mounts'):
            dev, mnt, fs, opts = l.split()[:4]
            mnt = mnt.replace('\\040', ' ')
            if (path == mnt or path.startswith(mnt.rstrip('/') + '/')) and (not best or len(mnt) > len(best[1])):
                best = (dev, mnt, fs, opts.split(','))
    except OSError:
        pass
    return best


def live():
    return os.path.exists('/run/casper') or os.path.isdir(MEDIUM + '/casper')


def _usb_root():
    """Cartella scrivibile sulla chiavetta, oppure None."""
    if 'root' in _cache:
        return _cache['root']
    root = None
    m = _mount_of(MEDIUM) if os.path.isdir(MEDIUM + '/casper') else None
    if m and m[2] in ('vfat', 'exfat', 'ntfs', 'ntfs3', 'fuseblk', 'ext4', 'ext2', 'btrfs'):
        if 'ro' in m[3]:
            _run(['sudo', '-n', 'mount', '-o', 'remount,rw', MEDIUM])
        m = _mount_of(MEDIUM)
        if m and 'rw' in m[3]:
            d = os.path.join(MEDIUM, 'rise-dati')
            _run(['sudo', '-n', 'mkdir', '-p', d])
            if os.path.isdir(d):
                root = d
    if not root:                       # chiavetta a immagine: partizione di dati, se c'è
        for label in ('writable', 'casper-rw', 'RISE-DATI'):
            rc, dev = _run(['blkid', '-L', label])
            if rc == 0 and dev:
                mnt = '/media/rise-dati'
                if not os.path.ismount(mnt):
                    _run(['sudo', '-n', 'mkdir', '-p', mnt])
                    rc, fs = _run(['blkid', '-o', 'value', '-s', 'TYPE', dev])
                    opts = ['-o', 'uid=%d,gid=%d' % (os.getuid(), os.getgid())] if fs in ('exfat', 'vfat', 'ntfs') else []
                    _run(['sudo', '-n', 'mount'] + opts + [dev, mnt])
                if os.path.ismount(mnt):
                    d = os.path.join(mnt, 'rise-dati')
                    u = pwd.getpwuid(os.getuid()).pw_name
                    _run(['sudo', '-n', 'mkdir', '-p', d])
                    _run(['sudo', '-n', 'chown', u + ':', d])
                    root = d
                    break
    _cache['root'] = root
    return root


def _boot_disk():
    """Il disco (chiavetta) da cui è partito Rise, se è una chiavetta USB scritta "a immagine"."""
    m = _mount_of(MEDIUM)
    if not m or not m[0].startswith('/dev/'):
        return None
    rc, pk = _run(['lsblk', '-no', 'PKNAME', m[0]])
    disk = '/dev/' + pk.splitlines()[0].strip() if rc == 0 and pk.strip() else m[0]
    rc, tran = _run(['lsblk', '-dno', 'TRAN', disk])
    if tran.strip() != 'usb':
        return None
    return disk


def usb_free_gb():
    """Spazio libero (non usato da nessuna partizione) sulla chiavetta di Rise, in GB. 0 se non si può usare."""
    if 'free' in _cache:
        return _cache['free']
    _cache['free'] = 0
    if not live() or _usb_root():
        return 0
    disk = _boot_disk()
    if not disk:
        return 0
    import json as _j
    rc, out = _run(['sudo', '-n', 'sfdisk', '-J', disk])
    rc2, size = _run(['blockdev', '--getsize64', disk])
    if rc2 != 0:
        rc2, size = _run(['sudo', '-n', 'blockdev', '--getsize64', disk])
    try:
        t = _j.loads(out)['partitiontable']
        end = max(p['start'] + p['size'] for p in t['partitions']) * t.get('sectorsize', 512)
        free = (int(size) - end) / 1e9
    except Exception:
        return 0
    _cache['free'] = round(free, 1) if free > 2 else 0
    return _cache['free']


def prepare_usb():
    """Crea nello spazio libero della chiavetta una partizione "RISE-DATI" (exFAT, leggibile anche da Windows)
    dove salvare modelli e voci. Le partizioni di Rise non vengono toccate."""
    disk = _boot_disk()
    _cache.pop('free', None)
    if not disk or not usb_free_gb():
        return {'ok': False, 'message': 'Su questa chiavetta non c’è spazio libero da usare.'}
    rc, before = _run(['lsblk', '-lnpo', 'NAME', disk])
    p = subprocess.run(['sudo', '-n', 'sfdisk', '--append', '--no-reread', '--no-tell-kernel', disk],
                       input=',,EBD0A0A2-B9E5-4433-87C0-68B6B72699C7\n', capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        # tabelle MBR semplici: tipo "7" (exFAT/NTFS)
        p = subprocess.run(['sudo', '-n', 'sfdisk', '--append', '--no-reread', '--no-tell-kernel', disk],
                           input=',,7\n', capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            return {'ok': False, 'message': 'Non riesco a preparare la chiavetta. (%s)' % (p.stderr.strip()[-200:])}
    _run(['sudo', '-n', 'partx', '-a', disk])
    _run(['sudo', '-n', 'udevadm', 'settle'], timeout=30)
    rc, after = _run(['lsblk', '-lnpo', 'NAME', disk])
    new = [d for d in after.split() if d not in before.split()]
    if not new:
        return {'ok': False, 'message': 'La chiavetta è pronta, ma serve riavviare il computer per usarla.'}
    dev = new[-1]
    rc, _ = _run(['sudo', '-n', 'mkfs.exfat', '-L', 'RISE-DATI', dev], timeout=600)
    if rc != 0:
        rc, _ = _run(['sudo', '-n', 'mkfs.ext4', '-F', '-L', 'RISE-DATI', dev], timeout=600)
    if rc != 0:
        return {'ok': False, 'message': 'Non riesco a formattare lo spazio della chiavetta.'}
    _cache.clear()
    return {'ok': bool(_usb_root()), 'message': '' if _usb_root() else 'Fatto: riavvia il computer per usarlo.'}


def data_dir(sub):
    """Cartella per i file grandi di un certo tipo ('models', 'voce'): (percorso, sulla_chiavetta)."""
    if live():
        root = _usb_root()
        if root:
            d = os.path.join(root, sub)
            if not os.path.isdir(d):
                if _run(['mkdir', '-p', d])[0] != 0:
                    _run(['sudo', '-n', 'mkdir', '-p', d])
            if os.path.isdir(d):
                return d, True
    d = os.path.join(HOME, '.local', 'share', 'rise', sub)
    os.makedirs(d, exist_ok=True)
    return d, False


def fs_type(path):
    m = _mount_of(path)
    return m[2] if m else ''


def too_big(path, size):
    return fs_type(path) == 'vfat' and size > FAT_LIMIT


# ---------- scrittura (come utente se si può, altrimenti come root) ----------
class _SudoFile:
    def __init__(self, path, append):
        self.p = subprocess.Popen(['sudo', '-n', 'tee'] + (['-a'] if append else []) + [path],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, b):
        self.p.stdin.write(b)

    def close(self):
        try:
            self.p.stdin.close()
        finally:
            self.p.wait()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def open_write(path, append=False):
    try:
        return open(path, 'ab' if append else 'wb')
    except PermissionError:
        return _SudoFile(path, append)


def replace(src, dst):
    try:
        os.replace(src, dst)
    except PermissionError:
        _run(['sudo', '-n', 'mv', '-f', src, dst])


def remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except PermissionError:
        _run(['sudo', '-n', 'rm', '-f', path])


def sync():
    """Scrive davvero sulla chiavetta (così si può staccare senza perdere niente)."""
    _run(['sync'], timeout=300)
