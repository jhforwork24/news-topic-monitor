from .ablenews import AblenewsAdapter
from .beminor import BeminorAdapter
from .chosun import ChosunAdapter
from .donga import DongaAdapter
from .hani import HaniAdapter
from .joongang import JoongangAdapter
from .khan import KhanAdapter
from .labortoday import LabortodayAdapter
from .mediaus import MediausAdapter
from .newscham import NewschamAdapter
from .ohmynews import OhmynewsAdapter
from .pressian import PressianAdapter
from .sisain import SisainAdapter
from .theindigo import TheindigoAdapter

ALL_ADAPTERS = (
    ChosunAdapter,
    JoongangAdapter,
    DongaAdapter,
    HaniAdapter,
    KhanAdapter,
    OhmynewsAdapter,
    PressianAdapter,
    SisainAdapter,
    NewschamAdapter,
    LabortodayAdapter,
    MediausAdapter,
    BeminorAdapter,
    AblenewsAdapter,
    TheindigoAdapter,
)

__all__ = [
    "ALL_ADAPTERS",
    "AblenewsAdapter",
    "BeminorAdapter",
    "ChosunAdapter",
    "DongaAdapter",
    "HaniAdapter",
    "JoongangAdapter",
    "KhanAdapter",
    "LabortodayAdapter",
    "MediausAdapter",
    "NewschamAdapter",
    "OhmynewsAdapter",
    "PressianAdapter",
    "SisainAdapter",
    "TheindigoAdapter",
]
