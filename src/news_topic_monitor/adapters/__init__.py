from .chosun import ChosunAdapter
from .donga import DongaAdapter
from .hani import HaniAdapter
from .joongang import JoongangAdapter

ALL_ADAPTERS = (ChosunAdapter, JoongangAdapter, DongaAdapter, HaniAdapter)

__all__ = [
    "ALL_ADAPTERS",
    "ChosunAdapter",
    "DongaAdapter",
    "HaniAdapter",
    "JoongangAdapter",
]
