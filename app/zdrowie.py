"""Kanał o zdrowiu po 50 (EN, długie filmy ze stickmanem Waltem): ten sam silnik co długie filmy PozaSkali (dlugi.py),
załadowany drugi raz pod nazwą „dlugi_zdrowie” – własny stan, katalog roboczy, adresy /api/zdrowie… i konto YouTube z grupy „zdrowie”."""
import importlib.util, os, sys

_spec = importlib.util.spec_from_file_location("dlugi_zdrowie", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dlugi.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["dlugi_zdrowie"] = _mod
_spec.loader.exec_module(_mod)
router = _mod.router
