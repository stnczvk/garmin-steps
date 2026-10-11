// Widget „Dziennik treningowy” dla Scriptable – kalorie dziś, białko, trening, waga.
// Ustawienie: dodaj widget Scriptable na ekran główny, przytrzymaj → Edytuj widżet → Script: ten skrypt.
const URL_DANE = "__URL__";
const APP = "__APP__";
const C = {bg1: new Color("#121019"), bg2: new Color("#08090A"), ink: new Color("#F4F4F5"), mut: new Color("#A1A1AA"),
  faint: new Color("#6B6B75"), acc: new Color("#9E8CFC"), acc2: new Color("#6E8BFF"), bad: new Color("#FF7B70"),
  good: new Color("#4ADE80"), p: new Color("#FF8A73"), track: new Color("#FFFFFF", 0.08)};
const DNI = ["nd", "pn", "wt", "śr", "cz", "pt", "sb"];

async function dane() {
  const fm = FileManager.local(), cache = fm.joinPath(fm.documentsDirectory(), "dziennik-widget.json");
  try { const r = new Request(URL_DANE); r.timeoutInterval = 15; const d = await r.loadJSON(); fm.writeString(cache, JSON.stringify(d)); return d; }
  catch (e) { return fm.fileExists(cache) ? JSON.parse(fm.readString(cache)) : null; }
}
function ring(pct, over, size) {
  const dc = new DrawContext(); dc.size = new Size(size, size); dc.opaque = false; dc.respectScreenScale = true;
  const lw = size * 0.11, r = size / 2 - lw / 2 - 1, cx = size / 2, cy = size / 2;
  const arc = (from, to, col) => { const p = new Path(); const n = Math.max(2, Math.round((to - from) * 90));
    for (let i = 0; i <= n; i++) { const a = -Math.PI / 2 + 2 * Math.PI * (from + (to - from) * i / n); const pt = new Point(cx + r * Math.cos(a), cy + r * Math.sin(a)); i ? p.addLine(pt) : p.move(pt); }
    dc.addPath(p); dc.setStrokeColor(col); dc.setLineWidth(lw); dc.strokePath(); };
  arc(0, 1, C.track);
  if (pct > 0) arc(0, Math.min(1, pct), over ? C.bad : C.acc);
  return dc.getImage();
}
const t = (st, s, size, w, col) => { const x = st.addText(s); x.font = Font.semiboldRoundedSystemFont(size); if (w === "b") x.font = Font.boldRoundedSystemFont(size); if (w === "r") x.font = Font.mediumRoundedSystemFont(size); x.textColor = col || C.ink; x.lineLimit = 1; x.minimumScaleFactor = 0.6; return x; };
const n = v => Math.round(v).toLocaleString("pl-PL");

const d = await dane();
const w = new ListWidget();
const g = new LinearGradient(); g.colors = [C.bg1, C.bg2]; g.locations = [0, 1]; w.backgroundGradient = g;
w.url = APP; w.setPadding(14, 14, 14, 14);
w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
const size = config.widgetFamily || "medium";
if (!d) { t(w, "Dziennik", 14, "b"); w.addSpacer(4); t(w, "Brak połączenia", 12, "r", C.mut); }
else {
  const z = new Date(), today = `${z.getFullYear()}-${String(z.getMonth() + 1).padStart(2, "0")}-${String(z.getDate()).padStart(2, "0")}`, fresh = d.date === today;
  const kc = fresh ? d.total.kcal : 0, goal = d.goal?.kcal, pr = fresh ? d.total.p : 0;
  const pg = Array.isArray(d.goal?.p) ? d.goal.p[0] : d.goal?.p;
  const main = w.addStack(); main.centerAlignContent();
  // pierścień kalorii
  if (size !== "small") {
    const ringBox = main.addStack(); ringBox.size = new Size(92, 92);
    ringBox.backgroundImage = ring(goal ? kc / goal : 0, goal && kc > goal, 92); ringBox.layoutVertically(); ringBox.centerAlignContent();
    ringBox.addSpacer(); const r1 = ringBox.addStack(); r1.addSpacer(); t(r1, n(kc), 19, "b"); r1.addSpacer();
    const r2 = ringBox.addStack(); r2.addSpacer(); t(r2, goal ? `z ${n(goal)}` : "kcal", 10, "r", C.mut); r2.addSpacer(); ringBox.addSpacer();
    main.addSpacer(14);
  }
  const col = main.addStack(); col.layoutVertically();
  const head = col.addStack(); head.centerAlignContent();
  t(head, "DZIENNIK", 10, "b", C.acc); head.addSpacer();
  if (size === "small") { t(col, `${n(kc)} kcal`, 22, "b"); t(col, goal ? (kc <= goal ? `zostało ${n(goal - kc)}` : `ponad o ${n(kc - goal)}`) : "dziś", 11, "r", goal && kc > goal ? C.bad : C.mut); }
  else { const left = goal ? goal - kc : null; t(col, left == null ? "Kalorie dziś" : left >= 0 ? `Zostało ${n(left)} kcal` : `Ponad o ${n(-left)} kcal`, 14, "b", left != null && left < 0 ? C.bad : C.ink); }
  col.addSpacer(6);
  // białko
  const pb = col.addStack(); pb.centerAlignContent(); t(pb, "Białko ", 11, "r", C.mut); t(pb, `${pr}${pg ? " / " + pg : ""} g`, 11, "s", C.p);
  // trening
  col.addSpacer(3);
  const tr = d.tr, trs = col.addStack(); trs.centerAlignContent();
  if (tr && fresh && tr.done) { t(trs, "✓ ", 11, "b", C.good); t(trs, `Trening ${tr.day} zrobiony`, 11, "s", C.ink); }
  else if (tr) { const td = tr.done ? tr.nextDate : tr.date, dd = td ? new Date(td + "T12:00:00") : null; t(trs, "Trening ", 11, "r", C.mut); t(trs, `${tr.done ? tr.next : tr.day}${dd ? (td === today ? " · dziś" : " · " + DNI[dd.getDay()]) : ""}`, 11, "s", C.acc); }
  if (d.bw && size !== "small") { col.addSpacer(3); const bs = col.addStack(); t(bs, "Waga ", 11, "r", C.mut); t(bs, `${String(d.bw).replace(".", ",")} kg`, 11, "s", C.ink); }
  if (!fresh) { w.addSpacer(4); t(w, "Brak wpisów z dziś", 9, "r", C.faint); }
}
Script.setWidget(w);
if (!config.runsInWidget) await (size === "small" ? w.presentSmall() : w.presentMedium());
Script.complete();
