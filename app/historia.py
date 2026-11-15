"""Kanał historyczny (EN, długie spokojne filmy o jednym wąskim temacie): ten sam silnik co długie filmy (dlugi.py),
załadowany trzeci raz pod nazwą „dlugi_historia” – własny stan, katalog, adresy /api/historia… i konto YouTube z grupy „historia”."""
import importlib.util, os, sys

_spec = importlib.util.spec_from_file_location("dlugi_historia", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dlugi.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["dlugi_historia"] = _mod
_spec.loader.exec_module(_mod)
router = _mod.router
