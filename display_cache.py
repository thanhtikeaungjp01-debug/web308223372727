"""Short-lived cache of public display metadata; never cache account/bid state."""
from copy import deepcopy
from functools import wraps
from threading import RLock
import time

_LOCK = RLock()
TTL_SECONDS = 15


def display_state(db):
    with _LOCK:
        cached = getattr(db, '_display_cache', None)
        if cached and cached[0] > time.monotonic():
            return deepcopy(cached[1])
        state = db.get_public_media()
        state.update(rocket_show=db.get_rocket_show(),
                     card_update_show=db.get_card_update_show(),
                     wheel_show=db.get_wheel_show())
        db._display_cache = (time.monotonic() + TTL_SECONDS, state)
        return deepcopy(state)


def invalidate_display(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        finally:
            with _LOCK:
                self._display_cache = None
    return wrapped
