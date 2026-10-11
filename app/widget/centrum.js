// Widget „Centrum dowodzenia” dla Scriptable – obserwujący, wyświetlenia 28 dni, post, który się rozkręca.
const URL_DANE = "__URL__";
const APP = "__APP__";
const C = {bg1: new Color("#121019"), bg2: new Color("#08090A"), ink: new Color("#F4F4F5"), mut: new Color("#A1A1AA"),
  faint: new Color("#6B6B75"), acc: new Color("#9E8CFC"), up: new Color("#4ADE80"), down: new Color("#FF7B70"), warn: new Color("#F5B25A"),
  yt: new Color("#E8504A"), ig: new Color("#9B66E8"), tt: new Color("#14A898")};

async function dane() {
  const fm = FileManager.local(), cache = fm.joinPath(fm.documentsDirectory(), "centrum-widget.json");
  try { const r = new Request(URL_DANE); r.timeoutInterval = 15; const d = await r.loadJSON(); if (d.error) throw 0; fm.writeString(cache, JSON.stringify(d)); return d; }
  catch (e) { return fm.fileExists(cache) ? JSON.parse(fm.readString(cache)) : null; }
}
function spark(v, w, h) {
  const dc = new DrawContext(); dc.size = new Size(w, h); dc.opaque = false; dc.respectScreenScale = true;
  if (v.length < 2) return dc.getImage();
  const mx = Math.max(...v, 1), mn = Math.min(...v), sp = (mx - mn) || 1;
  const pts = v.map((y, i) => new Point(i / (v.length - 1) * w, h - 3 - (y - mn) / sp * (h - 8)));
  const area = new Path(); area.move(new Point(0, h)); pts.forEach(p => area.addLine(p)); area.addLine(new Point(w, h)); area.closeSubpath();
  dc.addPath(area); dc.setFillColor(new Color("#9E8CFC", 0.16)); dc.fillPath();
  const ln = new Path(); pts.forEach((p, i) => i ? ln.addLine(p) : ln.move(p));
  dc.addPath(ln); dc.setStrokeColor(C.acc); dc.setLineWidth(2.2); dc.strokePath();
  const l = pts[pts.length - 1]; dc.setFillColor(C.acc); dc.fillEllipse(new Rect(l.x - 3, l.y - 3, 6, 6));
  return dc.getImage();
}
const t = (st, s, size, w, col) => { const x = st.addText(String(s)); x.font = w === "b" ? Font.boldRoundedSystemFont(size) : w === "r" ? Font.mediumRoundedSystemFont(size) : Font.semiboldRoundedSystemFont(size); x.textColor = col || C.ink; x.lineLimit = 1; x.minimumScaleFactor = 0.6; return x; };
const n = v => v >= 1e6 ? (v / 1e6).toFixed(1).replace(".", ",") + " mln" : v >= 1e5 ? (v / 1e3).toFixed(0).replace(".", ",") + " tys." : Math.round(v).toLocaleString("pl-PL");

const d = await dane();
const w = new ListWidget();
const g = new LinearGradient(); g.colors = [C.bg1, C.bg2]; g.locations = [0, 1]; w.backgroundGradient = g;
w.url = APP; w.setPadding(14, 14, 14, 14);
w.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);
const size = config.widgetFamily || "medium";
if (!d) { t(w, "Centrum", 14, "b"); w.addSpacer(4); t(w, "Brak połączenia", 12, "r", C.mut); }
else {
  const head = w.addStack(); head.centerAlignContent();
  t(head, "CENTRUM", 10, "b", C.acc); head.addSpacer();
  if (d.hot && size !== "small") t(head, "🔥", 11, "b");
  w.addSpacer(6);
  const row = w.addStack(); row.bottomAlignContent();
  const left = row.addStack(); left.layoutVertically();
  t(left, "Obserwujący", 10, "r", C.mut);
  t(left, n(d.followers), size === "small" ? 24 : 26, "b");
  const ch = left.addStack(); ch.centerAlignContent();
  const dl = d.delta7 || 0;
  t(ch, `${dl > 0 ? "▲" : dl < 0 ? "▼" : "■"} ${n(Math.abs(dl))}`, 11, "s", dl > 0 ? C.up : dl < 0 ? C.down : C.faint); t(ch, " 7 dni", 11, "r", C.faint);
  if (size !== "small") {
    row.addSpacer(16);
    const right = row.addStack(); right.layoutVertically();
    const rh = right.addStack(); t(rh, "Wyświetlenia 28 dni  ", 10, "r", C.mut); t(rh, n(d.views28), 10, "b", C.ink);
    right.addSpacer(4);
    const img = right.addImage(spark(d.series || [], 150, 44)); img.imageSize = new Size(150, 44);
  }
  w.addSpacer();
  if (d.hot && size !== "small") {
    const hs = w.addStack(); hs.centerAlignContent(); hs.backgroundColor = new Color("#F5B25A", 0.12); hs.cornerRadius = 8; hs.setPadding(4, 8, 4, 8);
    t(hs, "🔥 ", 10, "b"); t(hs, `${String(d.hot.ratio).replace(".", ",")}× · ${d.hot.title || "Post"}`, 10, "s", C.warn);
  } else if (size !== "small") {
    const ps = w.addStack(); ps.centerAlignContent();
    (d.per || []).slice(0, 4).forEach((a, i) => { if (i) ps.addSpacer(10); const dot = ps.addText("●"); dot.font = Font.boldSystemFont(8); dot.textColor = C[{youtube: "yt", instagram: "ig", tiktok: "tt"}[a.platform]] || C.acc; ps.addSpacer(3); t(ps, n(a.followers || 0), 10, "s", C.mut); });
  }
}
Script.setWidget(w);
if (!config.runsInWidget) await (size === "small" ? w.presentSmall() : w.presentMedium());
Script.complete();
