"""wmslib: a small, dependency-light library for the WAREMA WMS USB stick (pyserial only).

    from wmslib import Stick, decode, report, snr_to_hex

* ``wmslib.codec``    frame builders, value encodings, decoder (no I/O)
* ``wmslib.commands`` command objects (frame + how to wait for the answer)
* ``wmslib.driver``   serial driver with command queue, polling and callbacks

Protocol notes: PROTOCOL.md and MEMORY_MAP.md next to this package.
"""

from .codec import *          # noqa: F401,F403
from .commands import *       # noqa: F401,F403
from .driver import Blind, Stick, serial   # noqa: F401
