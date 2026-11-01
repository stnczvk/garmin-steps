// motyw „Kosmos” + czcionka nagłówków – na każdej stronie
(function(){try{var h=document.head||document.documentElement;
 var f=document.createElement('link');f.rel='stylesheet';f.href='https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500..800&display=swap';h.appendChild(f);
 var l=document.createElement('link');l.rel='stylesheet';l.href='/theme.css';l.id='kosmos';h.appendChild(l);}catch(e){}})();
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
  var C={v:'#6e8bff',p:'#ff6fa5',b:'#5ad1ff',g:'#3ddc84',y:'#ffc24b'};
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
   +'.mfh:not([data-mf]){flex-wrap:wrap!important;row-gap:10px}.mfh>[data-mf]{flex:1 1 100%!important;min-width:0;width:100%}.mfh .seg{max-width:100%;overflow-x:auto;scrollbar-width:none}.mfh .seg::-webkit-scrollbar{display:none}.mfh .seg button{flex:none}'
   +'.mfx{margin-left:auto;flex:none;width:26px;height:26px;border-radius:8px;display:grid;place-items:center;color:var(--faint,var(--fa,#6f6e86));background:rgba(127,127,160,.12);transition:transform .2s}'
   +'.mfx svg{width:15px;height:15px}.mf .mfx{transform:rotate(-90deg)}.mf>:not(.mfh){display:none!important}.mf .mfh,.mf [data-mf]{margin-bottom:0!important}'
   +'[data-mf] .hxw{white-space:nowrap;flex:none}[data-mf]>:not(.hxw):not(.mfx):not(.hx):not(.hi){min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:0 1 auto}';
  function go(){if(document.body.hasAttribute('data-nofold'))return;var el=document.createElement('style');el.textContent=css;document.head.appendChild(el);sch();new MutationObserver(sch).observe(document.body,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();
})();

/* rosnące liczby: duże wartości w kafelkach liczą się od 0 przy pierwszym pokazaniu (raz na element) */
(function(){
  if(window.matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches)return;
  var SEL='.kv,.kpi .v,.kpi>b,.kpis .v,.kpis b,.kn,[data-count]';
  var RX=/^([^\d\-]*)(-?\d{1,3}(?:[   ]\d{3})+|-?\d+)(?:([.,])(\d+))?(.*)$/;
  function fmt(v,dec,sep,grp){var s=Math.abs(v).toFixed(dec),p=s.split('.'),i=p[0];
    if(grp)i=i.replace(/\B(?=(\d{3})+(?!\d))/g,grp);return (v<0?'-':'')+i+(dec?sep+p[1]:'')}
  function anim(el){
    if(el.dataset.cnt)return;var t=(el.textContent||'').trim();var m=t.match(RX);
    if(!m||el.children.length>1){return}
    var raw=m[2].replace(/[   ]/g,''),dec=m[4]?m[4].length:0,v=parseFloat(raw+(dec?'.'+m[4]:''));
    if(!isFinite(v)||Math.abs(v)<2)return;
    el.dataset.cnt=1;var gm=m[2].match(/[   ]/),grp=gm?gm[0]:(Math.abs(v)>=10000?' ':'');
    var pre=m[1],suf=m[5],sep=m[3]||',',node=el.firstChild&&el.firstChild.nodeType===3&&el.childNodes.length===1?el.firstChild:null;
    if(!node)return;var t0=null,D=Math.min(1400,600+Math.log10(Math.abs(v)+1)*180);
    el.style.fontVariantNumeric='tabular-nums';
    function step(ts){if(!t0)t0=ts;var k=Math.min(1,(ts-t0)/D),e=1-Math.pow(1-k,3);
      if(node.textContent!==cur&&k>0){return}   // ktoś podmienił tekst w trakcie – przerwij
      cur=pre+fmt(v*e,dec,sep,grp)+suf;node.textContent=cur;if(k<1)requestAnimationFrame(step);else el.style.fontVariantNumeric=''}
    var cur=pre+fmt(0,dec,sep,grp)+suf;node.textContent=cur;requestAnimationFrame(step)}
  var io=window.IntersectionObserver?new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){io.unobserve(e.target);anim(e.target)}})}):null;
  function scan(){document.querySelectorAll(SEL).forEach(function(el){if(el.dataset.cnt||el.dataset.cw)return;
    if(!/\d/.test(el.textContent||''))return;el.dataset.cw=1;if(io)io.observe(el);else anim(el)})}
  var q=0;function sch(){if(q)return;q=setTimeout(function(){q=0;scan()},60)}
  function go(){scan();new MutationObserver(sch).observe(document.body,{childList:true,subtree:true,characterData:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();
})();

/* karty: cień i podświetlona górna krawędź; wejście tylko raz przy starcie strony (same karty obecne od razu, bez przesuwania później dorysowanych) */
(function(){
  var RM=window.matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches;
  var css='.gc-sh{box-shadow:inset 0 1px 0 rgba(255,255,255,.07),0 1px 0 rgba(0,0,0,.25),0 18px 40px -24px rgba(0,0,0,.75)!important}'
   +':root[data-theme=light] .gc-sh{box-shadow:inset 0 1px 0 rgba(255,255,255,.9),0 1px 2px rgba(0,0,0,.05),0 14px 34px -22px rgba(30,30,60,.25)!important}'
   +'@keyframes gcin{from{opacity:0}to{opacity:1}}.gc-in{animation:gcin .45s ease-out both;animation-delay:var(--gd,0ms)}';
  var seen=new WeakSet(),first=true,n=0;
  function isCard(e,cs){var r=parseFloat(cs.borderTopLeftRadius);if(r<14)return false;
    if(cs.backgroundImage==='none'&&(/rgba\(0, 0, 0, 0\)|transparent/.test(cs.backgroundColor)))return false;
    if(cs.position==='fixed'||cs.position==='absolute')return false;return e.offsetWidth>=140&&e.offsetHeight>=56}
  function skip(e){return e.closest('#side,.side,.tbar,nav,dialog,.modal,.mod,#hm,#panel,.panel,header,.head,button,a,input,textarea,select,.vframe')}
  function scan(){
    if(document.body.hasAttribute('data-intro'))first=false;
    var all=document.querySelectorAll('main *,.wrap *,body>section,body>div *');
    for(var i=0;i<all.length;i++){var e=all[i];if(seen.has(e))continue;
      var p=e.parentElement,inCard=false;while(p&&p!==document.body){if(p.dataset&&p.dataset.gc){inCard=true;break}p=p.parentElement}
      if(inCard){seen.add(e);continue}
      if(!e.offsetParent)continue;
      var cs=getComputedStyle(e);seen.add(e);if(!isCard(e,cs)||skip(e))continue;
      e.dataset.gc=1;if(cs.boxShadow==='none'||/inset/.test(cs.boxShadow)&&!/, 0px \d+px/.test(cs.boxShadow))e.classList.add('gc-sh');
      if(first&&!RM&&e.getBoundingClientRect().top<innerHeight){e.style.setProperty('--gd',Math.min(n++,8)*40+'ms');e.classList.add('gc-in');
        (function(el){setTimeout(function(){el.classList.remove('gc-in');el.style.removeProperty('--gd')},1200)})(e)}}
    first=false}
  var q=0;function sch(){if(q)return;q=setTimeout(function(){q=0;scan()},120)}
  function go(){var s=document.createElement('style');s.textContent=css;document.head.appendChild(s);scan();
    new MutationObserver(sch).observe(document.body,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();
})();

/* potwierdzenia na dole ekranu (zamiast okienek alert) + podświetlenie ramki pod kursorem */
(function(){
  function box(){var b=document.getElementById('ktoast');if(!b){b=document.createElement('div');b.id='ktoast';document.body.appendChild(b)}return b}
  window.toast=function(msg,err){try{var d=document.createElement('div');if(err)d.className='er';d.innerHTML='<i>'+(err?'!':'✓')+'</i><span></span>';d.lastChild.textContent=String(msg);
    box().appendChild(d);setTimeout(function(){d.classList.add('out');setTimeout(function(){d.remove()},320)},err?5200:2600)}catch(e){}};
  var oa=window.alert;window.alert=function(m){if(!document.body)return oa.call(window,m);toast(m,/błąd|nie |error|brak|nie udało/i.test(String(m)))};
  if(!(window.matchMedia&&matchMedia('(hover:hover) and (pointer:fine)').matches))return;
  var raf=0,ev=null;
  addEventListener('pointermove',function(e){ev=e;if(raf)return;raf=requestAnimationFrame(function(){raf=0;
    var c=ev.target&&ev.target.closest&&ev.target.closest('[data-gc]');if(!c)return;
    var s=c.querySelector(':scope>.kspot');if(!s){if(getComputedStyle(c).position==='static')c.style.position='relative';s=document.createElement('i');s.className='kspot';c.appendChild(s)}
    var r=c.getBoundingClientRect();s.style.setProperty('--mx',(ev.clientX-r.left)+'px');s.style.setProperty('--my',(ev.clientY-r.top)+'px')})},{passive:true});
})();

/* telefon: przeciągnij w dół, żeby odświeżyć */
(function(){
  if(!('ontouchstart' in window))return;
  var pth=(location.pathname.split('/').pop()||'index').replace(/\.html$/,'');
  if(/^(notatki|agencja|skaner|hasla)$/.test(pth))return;
  var el,sy=null,sx=0,d=0,busy=false,MAX=90,TR=70;
  function ui(){if(el)return el;el=document.createElement('div');el.id='kptr';el.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>';document.body.appendChild(el);return el}
  function scrolledInside(t){for(var n=t;n&&n!==document.body;n=n.parentElement){if(n.scrollTop>0)return true;var tg=n.tagName;if(tg==='CANVAS'||tg==='TEXTAREA'||tg==='INPUT'||tg==='SELECT'||tg==='IFRAME')return true}return false}
  function top(){return (document.scrollingElement||document.documentElement).scrollTop<=0}
  addEventListener('touchstart',function(e){if(busy||e.touches.length>1||!top()||scrolledInside(e.target)){sy=null;return}
    if(pth==='index'&&document.body.classList.contains('v-ext')){sy=null;return}   // w zakładce odświeża ramka zakładki
    sy=e.touches[0].clientY;sx=e.touches[0].clientX;d=0},{passive:true});
  addEventListener('touchmove',function(e){if(sy==null)return;var dy=e.touches[0].clientY-sy,dx=e.touches[0].clientX-sx;
    if(dy<=0||Math.abs(dx)>dy){if(d===0){sy=null}return}
    d=Math.min(MAX,dy*.5);var u=ui();u.classList.add('on');u.style.transform='translate(-50%,'+(d-10)+'px) rotate('+(d*4)+'deg)';u.classList.toggle('go',d>=TR)},{passive:true});
  addEventListener('touchend',function(){if(sy==null)return;sy=null;var u=ui();
    if(d<TR){u.style.transform='translate(-50%,-60px)';u.classList.remove('on','go');d=0;return}
    busy=true;u.classList.add('spin');u.style.transform='translate(-50%,46px)';
    var done=function(){u.classList.remove('spin','go','on');u.style.transform='translate(-50%,-60px)';busy=false;d=0;if(window.toast)toast('Odświeżono')};
    try{
      if(pth==='index'){var b=document.getElementById('refresh');if(b){b.click();setTimeout(done,900);return}}
      if(typeof window.load==='function'){Promise.resolve(window.load()).then(function(){setTimeout(done,300)},function(){setTimeout(done,300)});return}
    }catch(x){}
    setTimeout(function(){location.reload()},350)});
})();
// ⌘K / Ctrl+K w zakładce → paleta w głównym oknie
addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && window.parent !== window) { e.preventDefault(); window.parent.postMessage({cmdk: 1}, location.origin); } });

/* ---------- efekty: gwiazdy w tle, konfetti, świecąca obwódka, rysujące się wykresy, przechylenie 3D ---------- */
(function(){
  var RM=window.matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches;
  var dark=function(){return document.documentElement.dataset.theme!=='light'};
  var st=document.createElement('style');
  st.textContent='#kstars{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:-1}'+
  '@property --ga{syntax:"<angle>";inherits:false;initial-value:0deg}'+
  '.kglow{position:relative}.kglow:after{content:"";position:absolute;inset:0;border-radius:inherit;padding:1.5px;pointer-events:none;z-index:2;'+
  'background:conic-gradient(from var(--ga),transparent 0 55%,#6e8bff 70%,#b59cff 82%,#ffc24b 90%,transparent 100%);'+
  '-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;animation:kga 4s linear infinite}'+
  '@keyframes kga{to{--ga:360deg}}'+
  '.kdraw{stroke-dasharray:1;stroke-dashoffset:1;animation:kdr 1.1s cubic-bezier(.3,.7,.2,1) forwards}@keyframes kdr{to{stroke-dashoffset:0}}'+
  '.kfill{opacity:0;animation:kfi .9s .35s ease forwards}@keyframes kfi{to{opacity:1}}'+
  '.kbar{transform-origin:bottom;animation:kbr .7s cubic-bezier(.3,.8,.3,1) both}@keyframes kbr{from{transform:scaleY(0)}}'+
  '.ktilt{transition:transform .25s ease;transform-style:preserve-3d;will-change:transform}'+
  '@media(prefers-reduced-motion:reduce){.kglow:after{animation:none}.kdraw,.kfill,.kbar{animation:none;stroke-dashoffset:0;opacity:1}}';
  (document.head||document.documentElement).appendChild(st);

  /* 1. gwiazdy */
  function stars(){
    if(RM||document.getElementById('kstars'))return;
    var c=document.createElement('canvas');c.id='kstars';document.body.prepend(c);
    var x=c.getContext('2d'),W,H,S=[],sh=null,next=performance.now()+6000,last=0,dpr=Math.min(2,window.devicePixelRatio||1);
    function size(){W=innerWidth;H=innerHeight;c.width=W*dpr;c.height=H*dpr;x.setTransform(dpr,0,0,dpr,0,0);
      var n=Math.round(Math.min(110,W*H/11000));S=[];for(var i=0;i<n;i++)S.push({x:Math.random()*W,y:Math.random()*H,r:Math.random()*1.2+.3,v:Math.random()*.06+.015,p:Math.random()*6.3,t:Math.random()*.02+.006})}
    size();addEventListener('resize',size);
    function live(){return !document.hidden&&window.CENTRUM_ACTIVE!==false&&dark()}
    function f(ts){requestAnimationFrame(f);if(ts-last<33)return;last=ts;
      if(!live()){if(c.style.display!=='none'){x.clearRect(0,0,W,H);c.style.display='none'}return}c.style.display='';
      x.clearRect(0,0,W,H);
      for(var i=0;i<S.length;i++){var s=S[i];s.y-=s.v;s.p+=s.t;if(s.y<-2){s.y=H+2;s.x=Math.random()*W}
        x.globalAlpha=.25+.45*(.5+.5*Math.sin(s.p));x.fillStyle=i%7?'#dfe4ff':'#c9b8ff';x.beginPath();x.arc(s.x,s.y,s.r,0,6.283);x.fill()}
      if(!sh&&ts>next){sh={x:Math.random()*W*.7+W*.2,y:Math.random()*H*.35,l:0};next=ts+12000+Math.random()*16000}
      if(sh){sh.l+=1;var k=sh.l/45,len=110,dx=-Math.cos(.5),dy=Math.sin(.5),hx=sh.x+dx*sh.l*9,hy=sh.y+dy*sh.l*9;
        var g=x.createLinearGradient(hx,hy,hx-dx*len,hy-dy*len);g.addColorStop(0,'rgba(255,255,255,'+(1-k)+')');g.addColorStop(1,'rgba(255,255,255,0)');
        x.globalAlpha=1;x.strokeStyle=g;x.lineWidth=1.6;x.beginPath();x.moveTo(hx,hy);x.lineTo(hx-dx*len,hy-dy*len);x.stroke();if(k>=1)sh=null}
      x.globalAlpha=1}
    requestAnimationFrame(f)}

  /* 3. konfetti */
  window.confetti=function(o){if(RM)return;o=o||{};var c=document.createElement('canvas');c.style.cssText='position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:99999';document.body.appendChild(c);
    var x=c.getContext('2d'),W=c.width=innerWidth,H=c.height=innerHeight,C=['#6e8bff','#b59cff','#ffc24b','#3ddc84','#ff6fa5','#5ad1ff'],P=[],ox=o.x!=null?o.x:W/2,oy=o.y!=null?o.y:H*.35,n=o.n||90;
    for(var i=0;i<n;i++){var a=Math.random()*6.283,sp=Math.random()*8+3;P.push({x:ox,y:oy,vx:Math.cos(a)*sp,vy:Math.sin(a)*sp-5,w:Math.random()*6+4,h:Math.random()*4+3,r:Math.random()*6,vr:Math.random()*.3-.15,c:C[i%C.length]})}
    var t0=performance.now();(function f(ts){var k=(ts-t0)/1800;x.clearRect(0,0,W,H);
      P.forEach(function(p){p.vy+=.28;p.vx*=.985;p.x+=p.vx;p.y+=p.vy;p.r+=p.vr;x.save();x.globalAlpha=Math.max(0,1-k*k);x.translate(p.x,p.y);x.rotate(p.r);x.fillStyle=p.c;x.fillRect(-p.w/2,-p.h/2,p.w,p.h);x.restore()});
      if(k<1)requestAnimationFrame(f);else c.remove()})(t0)};
  window.confettiAt=function(el,n){try{var r=el.getBoundingClientRect();confetti({x:r.left+r.width/2,y:r.top+r.height/2,n:n||60})}catch(e){confetti()}};

  /* 5. wykresy rysują się przy pierwszym pokazaniu */
  var seen={},boot=performance.now();
  function key(el,i){var s=el.closest('[id]');return (s?s.id:'')+':'+i}
  function draw(svg){var b=svg.getBoundingClientRect();if(b.width<80)return;
    svg.querySelectorAll('path,polyline,polygon').forEach(function(p){if(p.classList.contains('sline')||p.classList.contains('ln'))return;
      var fl=p.getAttribute('fill');if(fl==='none'&&p.getAttribute('stroke')){p.setAttribute('pathLength','1');p.classList.add('kdraw')}else if(fl&&fl.indexOf('url(')===0)p.classList.add('kfill')})}
  function bars(box){[].forEach.call(box.children,function(i,n){i.classList.add('kbar');i.style.animationDelay=(n*25)+'ms'})}
  var io=window.IntersectionObserver?new IntersectionObserver(function(es){es.forEach(function(e){if(!e.isIntersecting)return;io.unobserve(e.target);
    var t=e.target;if(t.tagName.toLowerCase()==='svg')draw(t);else bars(t)})},{threshold:.2}):null;
  function scanCharts(){if(RM||!io)return;
    document.querySelectorAll('.t svg, .card svg, .eb').forEach(function(el,i){if(el.dataset.kch)return;el.dataset.kch=1;
      if(el.tagName.toLowerCase()==='svg'&&(el.getAttribute('viewBox')||'')==='0 0 24 24')return;
      var k=key(el,el.tagName+i);if(seen[k]&&performance.now()-boot>3000)return;seen[k]=1;io.observe(el)})}

  /* 6. przechylenie 3D (tylko mysz) */
  function tilt(){if(RM||!matchMedia('(hover:hover) and (pointer:fine)').matches)return;
    document.addEventListener('mousemove',function(e){var el=e.target.closest&&e.target.closest('.kp>.t,.wkg>div,[data-tilt]');
      if(tilt.cur&&tilt.cur!==el){tilt.cur.style.transform='';tilt.cur=null}if(!el)return;tilt.cur=el;el.classList.add('ktilt');
      var r=el.getBoundingClientRect(),px=(e.clientX-r.left)/r.width-.5,py=(e.clientY-r.top)/r.height-.5;
      el.style.transform='perspective(700px) rotateX('+(-py*7).toFixed(2)+'deg) rotateY('+(px*7).toFixed(2)+'deg) translateY(-2px)'},{passive:true});
    document.addEventListener('mouseleave',function(){if(tilt.cur){tilt.cur.style.transform='';tilt.cur=null}})}

  var q=0;function sch(){if(q)return;q=setTimeout(function(){q=0;scanCharts()},120)}
  function go(){stars();tilt();scanCharts();new MutationObserver(sch).observe(document.body,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();
})();

/* fala po kliknięciu w przycisk + ikonki kart reagują na najechanie */
(function(){
  if(window.matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches)return;
  var st=document.createElement('style');
  st.textContent='.krip{position:absolute;border-radius:50%;pointer-events:none;background:rgba(255,255,255,.35);transform:scale(0);opacity:1;animation:krip .55s ease-out forwards}@keyframes krip{to{transform:scale(1);opacity:0}}'+
  '.t>h3 .hi,.t>h3 .hi svg{transition:transform .4s cubic-bezier(.3,1.6,.5,1)}.t:hover>h3 .hi{transform:scale(1.1) rotate(-8deg)}'+
  '.qa button .hi{transition:transform .35s cubic-bezier(.3,1.6,.5,1)}.qa button:hover .hi{transform:translateY(-2px) scale(1.08)}';
  (document.head||document.documentElement).appendChild(st);
  document.addEventListener('pointerdown',function(e){var b=e.target.closest&&e.target.closest('.btn,.qa button,.kbtn');if(!b)return;
    var r=b.getBoundingClientRect(),d=Math.max(r.width,r.height)*2.2,s=document.createElement('span');s.className='krip';
    s.style.cssText='width:'+d+'px;height:'+d+'px;left:'+(e.clientX-r.left-d/2)+'px;top:'+(e.clientY-r.top-d/2)+'px';
    var cs=getComputedStyle(b);if(cs.position==='static')b.style.position='relative';if(cs.overflow!=='hidden')b.style.overflow='hidden';
    b.appendChild(s);setTimeout(function(){s.remove()},600)},{passive:true});
})();
