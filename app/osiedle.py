"""Kanał „Osiedle Tłumaczy” (PL, klockowe osiedle, dialogi postaci, sceny z Veo): ten sam silnik co długie filmy (dlugi.py),
załadowany pod nazwą „dlugi_osiedle” – własny stan, katalog, adresy /api/osiedle… i konto YouTube z grupy „osiedle”."""
import importlib.util, os, sys

_spec = importlib.util.spec_from_file_location("dlugi_osiedle", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dlugi.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["dlugi_osiedle"] = _mod
_spec.loader.exec_module(_mod)
router = _mod.router
