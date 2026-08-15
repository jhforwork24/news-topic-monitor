from .ablenews import AblenewsAdapter
from .beminor import BeminorAdapter
from .chosun import ChosunAdapter
from .donga import DongaAdapter
from .hani import HaniAdapter
from .joongang import JoongangAdapter
from .jtbc import JtbcAdapter
from .kbs import KbsAdapter
from .khan import KhanAdapter
from .labortoday import LabortodayAdapter
from .mbc import MbcAdapter
from .newscham import NewschamAdapter
from .ohmynews import OhmynewsAdapter
from .pressian import PressianAdapter
from .sbs import SbsAdapter
from .theindigo import TheindigoAdapter

ALL_ADAPTERS = (
    ChosunAdapter,
    JoongangAdapter,
    DongaAdapter,
    HaniAdapter,
    KhanAdapter,
    OhmynewsAdapter,
    PressianAdapter,
    NewschamAdapter,
    LabortodayAdapter,
    BeminorAdapter,
    AblenewsAdapter,
    TheindigoAdapter,
    KbsAdapter,
    MbcAdapter,
    SbsAdapter,
    JtbcAdapter,
)

__all__ = [
    "ALL_ADAPTERS",
    "AblenewsAdapter",
    "BeminorAdapter",
    "ChosunAdapter",
    "DongaAdapter",
    "HaniAdapter",
    "JoongangAdapter",
    "JtbcAdapter",
    "KbsAdapter",
    "KhanAdapter",
    "LabortodayAdapter",
    "MbcAdapter",
    "NewschamAdapter",
    "OhmynewsAdapter",
    "PressianAdapter",
    "SbsAdapter",
    "TheindigoAdapter",
]
