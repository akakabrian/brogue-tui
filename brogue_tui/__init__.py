"""brogue-tui — Textual re-shell over BrogueCE.

Package public surface is intentionally thin: the BrogueEngine class
(thread-owning engine handle) and BrogueApp (the Textual application).
"""

from .engine import BrogueEngine, COLS, ROWS, DCOLS, DROWS, MESSAGE_LINES

__all__ = [
    "BrogueEngine",
    "COLS",
    "ROWS",
    "DCOLS",
    "DROWS",
    "MESSAGE_LINES",
]
