"""Compatibility shim: the protocol code lives in the generic ``wmslib`` package."""

from wmslib import *   # noqa: F401,F403
from wmslib.codec import _round   # noqa: F401
from wmslib.driver import Blind, Stick, serial   # noqa: F401
