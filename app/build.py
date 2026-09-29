"""Buduje web/index.html z source.html (kod dziennika) + nagłówek PWA i most do serwera."""
from pathlib import Path
H = Path(__file__).parent
src = (H / "source.html").read_text(encoding="utf-8")
head = '''<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0D1015">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Dziennik">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>html{padding-top:env(safe-area-inset-top,0px)}body{margin:0}[hidden]{display:none!important}</style>
<script src="/shim.js"></script>
'''
out = head + src.replace("<title>Dziennik treningowy</title>", "<title>Dziennik treningowy</title></head><body>", 1) + "\n</body></html>\n"
(H / "web" / "index.html").write_text(out, encoding="utf-8")
print("index.html", len(out))
