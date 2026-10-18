"""Buduje web/index.html z source.html (kod dziennika) + nagłówek PWA i most do serwera."""
from pathlib import Path
H = Path(__file__).parent
src = (H / "source.html").read_text(encoding="utf-8")
head = '''<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#08090A">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/ikona-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Dziennik">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>html{padding-top:env(safe-area-inset-top,0px)}body{margin:0}[hidden]{display:none!important}</style>
<script src="/shim.js"></script>
'''
out = head + src.replace("<title>Dziennik treningowy</title>", "<title>Dziennik treningowy</title></head><body><div id=\"dz-splash\" style=\"position:fixed;inset:0;z-index:10000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;background:radial-gradient(60% 40% at 50% 0%,rgba(120,98,255,.22),transparent 70%),#08090A;transition:opacity .35s ease\"><img src=\"/ikona-192.png\" alt=\"\" width=\"84\" height=\"84\" style=\"border-radius:22px;box-shadow:0 18px 50px -14px rgba(126,105,245,.75);animation:dzsp 1.6s ease-in-out infinite\"><div style=\"font:650 22px/1.2 -apple-system,system-ui,sans-serif;letter-spacing:-.02em;color:#EEF1F5\">Dziennik treningowy</div><div style=\"width:120px;height:3px;border-radius:3px;background:rgba(255,255,255,.08);overflow:hidden\"><div style=\"width:40%;height:100%;border-radius:3px;background:linear-gradient(90deg,#B1A2FF,#7E69F5);animation:dzbar 1.1s ease-in-out infinite\"></div></div><style>@keyframes dzsp{50%{transform:scale(1.05)}}@keyframes dzbar{0%{transform:translateX(-100%)}100%{transform:translateX(250%)}}@media (prefers-reduced-motion:reduce){#dz-splash *{animation:none!important}}</style></div>", 1) + "\n</body></html>\n"
(H / "web" / "index.html").write_text(out, encoding="utf-8")
print("index.html", len(out))
