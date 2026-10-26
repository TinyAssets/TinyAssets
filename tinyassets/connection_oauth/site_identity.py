"""Site identity from the system Public Suffix List, never a last-two-label guess."""
import ctypes
from ctypes.util import find_library
from functools import lru_cache
from urllib.parse import urlsplit


@lru_cache(maxsize=1)
def _psl():
    name = find_library("psl")
    if not name:
        return None
    library = ctypes.CDLL(name)
    library.psl_builtin.restype = ctypes.c_void_p
    library.psl_registrable_domain.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    library.psl_registrable_domain.restype = ctypes.c_char_p
    return library


def site(url):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    library = _psl()
    if library and host:
        domain = library.psl_registrable_domain(library.psl_builtin(), host.encode("idna"))
        if domain:
            return domain.decode("ascii")
    return host
