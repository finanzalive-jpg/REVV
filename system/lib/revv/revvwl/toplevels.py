"""Elenco delle finestre aperte (protocollo wlr-foreign-toplevel di labwc).

Gira in un thread separato con una sua connessione Wayland: ogni volta che
una finestra si apre, cambia titolo o stato, o si chiude, chiama on_change
con l'elenco aggiornato. Le richieste (attiva, riduci, chiudi) si possono
fare da qualsiasi thread.
"""
import threading

from pywayland.client import Display
from pywayland.protocol.wayland import WlSeat

from .ftl import ZwlrForeignToplevelManagerV1


class Toplevels:
    def __init__(self, on_change):
        self.on_change = on_change
        self.items = {}      # id -> dict
        self.handles = {}    # id -> handle
        self.seat = None
        self.manager = None
        self.display = None
        self._next = 0
        self._lock = threading.Lock()
        self.available = False

    # ---------- avvio ----------
    def start(self):
        try:
            self.display = Display()
            self.display.connect()
        except Exception:
            return False
        # il registro va tenuto in vita: pywayland lo usa per creare le nuove finestre
        self.registry = reg = self.display.get_registry()
        reg.dispatcher['global'] = self._global
        self.display.roundtrip()
        if not self.manager:
            self.display.disconnect()
            return False
        self.available = True
        self.display.roundtrip()
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _global(self, registry, name, interface, version):
        if interface == 'zwlr_foreign_toplevel_manager_v1':
            self.manager = registry.bind(name, ZwlrForeignToplevelManagerV1, min(version, 3))
            self.manager.dispatcher['toplevel'] = self._new_toplevel
        elif interface == 'wl_seat' and self.seat is None:
            self.seat = registry.bind(name, WlSeat, 1)

    def _loop(self):
        while True:
            try:
                if self.display.dispatch(block=True) == -1:
                    break
            except Exception:
                break

    # ---------- eventi ----------
    def _new_toplevel(self, manager, handle):
        self._next += 1
        tid = self._next
        item = {'id': tid, 'title': '', 'app_id': '', 'activated': False, 'minimized': False,
                'maximized': False, 'ready': False}
        with self._lock:
            self.items[tid] = item
            self.handles[tid] = handle

        def title(h, t):
            item['title'] = t

        def app_id(h, a):
            item['app_id'] = a

        def state(h, arr):
            vals = list(arr) if not isinstance(arr, (bytes, bytearray)) else \
                [int.from_bytes(arr[i:i + 4], 'little') for i in range(0, len(arr), 4)]
            item['maximized'] = 0 in vals
            item['minimized'] = 1 in vals
            item['activated'] = 2 in vals

        def done(h):
            item['ready'] = True
            self._emit()

        def closed(h):
            with self._lock:
                self.items.pop(tid, None)
                self.handles.pop(tid, None)
            try:
                h.destroy()
            except Exception:
                pass
            self._emit()

        handle.dispatcher['title'] = title
        handle.dispatcher['app_id'] = app_id
        handle.dispatcher['state'] = state
        handle.dispatcher['done'] = done
        handle.dispatcher['closed'] = closed

    def _emit(self):
        with self._lock:
            lst = [dict(i) for i in self.items.values() if i['ready']]
        try:
            self.on_change(lst)
        except Exception:
            pass

    def snapshot(self):
        with self._lock:
            return [dict(i) for i in self.items.values() if i['ready']]

    # ---------- richieste ----------
    def _req(self, tid, fn):
        with self._lock:
            h = self.handles.get(int(tid))
        if not h:
            return False
        try:
            fn(h)
            self.display.flush()
            return True
        except Exception:
            return False

    def activate(self, tid):
        def f(h):
            h.unset_minimized()
            if self.seat is not None:
                h.activate(self.seat)
        return self._req(tid, f)

    def minimize(self, tid):
        return self._req(tid, lambda h: h.set_minimized())

    def close(self, tid):
        return self._req(tid, lambda h: h.close())
