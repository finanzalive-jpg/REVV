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
                    _run(['sudo', '-n', 'mount', dev, mnt])
                if os.path.ismount(mnt):
                    d = os.path.join(mnt, 'rise-dati')
                    u = pwd.getpwuid(os.getuid()).pw_name
                    _run(['sudo', '-n', 'mkdir', '-p', d])
                    _run(['sudo', '-n', 'chown', u + ':', d])
                    root = d
                    break
    _cache['root'] = root
    return root


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
