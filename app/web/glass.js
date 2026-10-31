// Wspólny skrypt zakładek centrum (ładowany w <head>, przed skryptami strony)
(function () {
  // 1) Oszczędzanie: zakładka ukryta w centrum (albo apka w tle) nie odświeża danych i nie renderuje 3D.
  window.CENTRUM_ACTIVE = true;
  const pending = new Map();  // jedna zaległa akcja na funkcję (inaczej po powrocie do karty odpalało się ich setki naraz)
  addEventListener('message', e => {
    if (e.origin !== location.origin || !e.data || typeof e.data.active !== 'boolean') return;
    window.CENTRUM_ACTIVE = e.data.active;
    if (e.data.active) flush();
  });
  const live = () => window.CENTRUM_ACTIVE !== false && !document.hidden;
  function flush() { const L = [...pending.values()]; pending.clear(); L.forEach((f, i) => setTimeout(() => { try { f(); } catch (x) {} }, i * 150)); }
  document.addEventListener('visibilitychange', () => { if (live()) flush(); });
  const si = window.setInterval.bind(window);
  window.setInterval = (fn, ms, ...a) => typeof fn !== 'function' ? si(fn, ms, ...a)
    : si(() => { if (live()) fn(...a); else pending.set(fn, () => fn(...a)); }, ms);

  // 1b) Animacje 3D: na telefonie max ~30 kl./s, a w ukrytej zakładce ~2 kl./s (zamiast 60 kl./s w tle)
  const rafN = window.requestAnimationFrame.bind(window), lastT = new Map();
  const slow = matchMedia('(hover: none)').matches || innerWidth < 760;
  window.requestAnimationFrame = cb => rafN(function w(t) {
    const gap = !live() ? 500 : slow ? 31 : 0, l = lastT.get(cb) || 0;
    if (gap && t - l < gap) return rafN(w);
    lastT.set(cb, t); cb(t);
  });

  // 2) Liquid Glass v2 – światło na szkle podąża za kursorem
  return; // efekt światła za kursorem wyłączony (v3)
  let raf = 0, ev = null;
  addEventListener('pointermove', e => {
    ev = e;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const el = ev.target.closest && ev.target.closest('.glass,.card,.kpi,.side');
      if (!el) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty('--mx', (ev.clientX - r.left) + 'px');
      el.style.setProperty('--my', (ev.clientY - r.top) + 'px');
    });
  }, { passive: true });
})();

/* v3: kolorowe kafelki ikon w nagłówkach – spójnie z ekranem głównym */
(function(){
  var pth=location.pathname.split('/').pop()||'';
  if(/^(home|notatki)/.test(pth))return;
  var P={
    users:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    eye:'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    trend:'<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    send:'<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/>',
    doc:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    pie:'<path d="M21 12A9 9 0 1 1 12 3v9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
    list:'<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
    cpu:'<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    zap:'<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    link:'<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    shield:'<path d="M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z"/><path d="M9 12l2 2 4-4"/>',
    film:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
    target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    key:'<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M17 6l3 3M15 8l2 2"/>',
    brief:'<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    act:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    bag:'<path d="M6 7h12l-1 13H7z"/><path d="M9 7a3 3 0 0 1 6 0"/>'
  };
  var C={v:'#8f7bff',p:'#ff6fa5',b:'#5ad1ff',g:'#3ddc84',y:'#ffc24b'};
  var K=[[/sprzeda|przych/i,'trend',C.g],[/personaliz/i,'users',C.p],[/produkt/i,'grid',C.b],[/zamówie/i,'list',C.y],[/obserwuj|follow/i,'users',C.p],[/wyświetl|zasięg|views/i,'eye',C.b],[/kapita|pnl|krzyw|wynik|wzrost|portfel/i,'trend',C.g],
    [/pora |kiedy|kalendarz|godzin|czas/i,'clock',C.y],[/post|publik/i,'send',C.b],[/raport|analityk|co działa|opis/i,'doc',C.v],
    [/platform|kierunek|reżim|sposób|udział|symbol/i,'pie',C.v],[/transakc|pozycj|aktywne|otwarte|kolejk|histori/i,'list',C.y],
    [/bot|strateg|lab/i,'cpu',C.v],[/sygna|alert/i,'zap',C.y],[/on-chain|korelac|etherscan/i,'link',C.b],[/strażnik|archiwist/i,'shield',C.g],
    [/montaż|zmontowan|film|vlog|zlecen|cheat|jedzeni|karuzel/i,'film',C.p],[/trener|cel/i,'target',C.g],[/powiadom/i,'bell',C.y],
    [/hasł/i,'key',C.g],[/agencj|biur/i,'brief',C.v],[/przegląd|statyst/i,'grid',C.v]];
  var EMO=/^\s*((?:\p{Extended_Pictographic}|\p{Regional_Indicator})(?:️|‍\p{Extended_Pictographic})*)\s*/u;
  function pick(t){for(var i=0;i<K.length;i++)if(K[i][0].test(t))return K[i];return [0,'act',C.v]}
  function svg(n){return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'+P[n]+'</svg>'}
  function deco(h,big){
    if(h.dataset.hx||h.querySelector('.hx,.hi'))return;var t=(h.textContent||'').trim();if(!t)return;
    h.dataset.hx=1;var k=pick(t),s=document.createElement('span');s.className='hx'+(big?' big':'');s.style.setProperty('--c',k[2]);
    var fe=h.firstElementChild;if(fe&&fe.tagName==='SPAN'&&!fe.className&&fe===[].find.call(h.childNodes,function(x){return x.nodeType===1||(x.nodeValue||'').trim()})){var hs=h;h=fe;}
    var fn=null;for(var c=h.firstChild;c;c=c.nextSibling){if(c.nodeType===3&&c.nodeValue.trim()){fn=c;break}if(c.nodeType===1&&(c.textContent||'').trim().length>2)break}
    if(fn&&EMO.test(fn.nodeValue)){var m=fn.nodeValue.match(EMO);s.textContent=m[1];s.classList.add('em');fn.nodeValue=fn.nodeValue.slice(m[0].length)}
    else s.innerHTML=svg(big&&H1[pth]?H1[pth]:k[1]);
    var ref=fn||h.firstChild,d=getComputedStyle(h).display;
    if(/flex|grid/.test(d)&&fn){var w=document.createElement('span');w.className='hxw';h.insertBefore(w,fn);w.appendChild(s);w.appendChild(fn)}
    else h.insertBefore(s,ref);
  }
  var H1={'montazownia':'film','skaner-stats':'trend','skaner-boty':'cpu','skaner-sygnaly':'zap','skaner':'act','agencja':'brief','straznicy':'shield','powiadomienia':'bell','pozaskala':'trend','hasla':'key','etsy':'bag','':'grid','index.html':'grid'};
  H1[pth.replace(/\.html$/,'')]&&(H1[pth]=H1[pth.replace(/\.html$/,'')]);
  function run(root){
    (root.querySelectorAll?root:document).querySelectorAll('h1,h2,h3').forEach(function(h){
      if(h.closest('#side,.tbar,nav,[data-noicon],.modal,dialog,#wm'))return;
      if(h.tagName==='H1'){if(h.closest('#login,.login'))return;deco(h,1);return}
      if(h.tagName==='H3'&&!EMO.test(h.textContent))return;
      if(parseFloat(getComputedStyle(h).fontSize)>21)return;
      deco(h);
    });
  }
  var q=0;function sch(){if(q)return;q=requestAnimationFrame(function(){q=0;run(document)})}
  function go(){if(document.body.hasAttribute('data-noicons'))return;sch();new MutationObserver(sch).observe(document.body,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();
})();

/* v3 telefon: karty zwijane po stuknięciu w nagłówek – krótsze strony; stan zapamiętywany */
(function(){
  if(!window.matchMedia||!matchMedia('(max-width:760px)').matches)return;
  var pth=(location.pathname.split('/').pop()||'index').replace(/\.html$/,'');
  if(/^(notatki|hasla)$/.test(pth))return;
  var KEY='mfold:'+pth,st={};try{st=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){}}
  var IMP=/czeka|aktywn|teraz|twój dzień|zaplanowane|zlecenia|montaże|portfel|krzywa|wyświetlenia dziennie|platformy|otwarte pozycje|strażnicy|kolejk/i,n=0;
  var CH='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
  function cardOf(h){var hd=h,c=h.parentElement;while(c&&c!==document.body){var s=getComputedStyle(c);
    if(parseFloat(s.borderTopLeftRadius)>=14&&s.backgroundImage!=='none')return [c,hd];hd=c;c=c.parentElement}return null}
  function run(){
    document.querySelectorAll('h2,h3').forEach(function(h){
      if(h.dataset.mf||h.closest('#side,.tbar,nav,dialog,.modal,details,.kp,.kpis,.k,[data-nofold]')||h.classList.contains('fold'))return;
      var r=cardOf(h);if(!r)return;var c=r[0],hd=r[1];
      if(hd===h&&c.firstElementChild!==h)return;
      if(c.querySelector('h2,h3')!==h||c.children.length<2)return;
      var t=(h.textContent||'').replace(/\s+/g,' ').trim().slice(0,40);if(!t)return;
      if(!c.dataset.mfs&&c.offsetHeight<150&&!(t in st))return;
      h.dataset.mf=1;c.dataset.mfs=1;hd.classList.add('mfh');
      var col=(t in st)?st[t]:(!IMP.test(t)&&(n++)>=1);
      var x=document.createElement('span');x.className='mfx';x.innerHTML=CH;h.appendChild(x);
      c.classList.toggle('mf',!!col);
      h.addEventListener('click',function(e){if(e.target.closest('button:not(.mfx),a,input,select,.seg,.sg,.chip[onclick]'))return;
        var v=!c.classList.contains('mf');c.classList.toggle('mf',v);st[t]=v;save()});
    });
  }
  var q=0;function sch(){if(q)return;q=setTimeout(function(){q=0;run()},120)}
  var css='[data-mf]{display:flex!important;align-items:center;justify-content:flex-start!important;cursor:pointer;-webkit-tap-highlight-color:transparent;gap:6px;flex-wrap:nowrap}'
   +'[data-mf]>.r{margin-left:auto!important;flex:none!important}[data-mf]>.r~.mfx{margin-left:8px}'
   +'.mfx{margin-left:auto;flex:none;width:26px;height:26px;border-radius:8px;display:grid;place-items:center;color:var(--faint,var(--fa,#6f6e86));background:rgba(127,127,160,.12);transition:transform .2s}'
   +'.mfx svg{width:15px;height:15px}.mf .mfx{transform:rotate(-90deg)}.mf>:not(.mfh){display:none!important}.mf .mfh,.mf [data-mf]{margin-bottom:0!important}'
   +'[data-mf] .hxw{white-space:nowrap;flex:none}[data-mf]>:not(.hxw):not(.mfx):not(.hx):not(.hi){min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:0 1 auto}';
  function go(){if(document.body.hasAttribute('data-nofold'))return;var el=document.createElement('style');el.textContent=css;document.head.appendChild(el);sch();new MutationObserver(sch).observe(document.body,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();
})();
