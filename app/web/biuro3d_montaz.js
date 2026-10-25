// Biuro 3D Montażowni – „Warsztat twórcy” (wybrany wygląd). Wymaga three.js r128 i <canvas id="c3">.
(function(){
if(!window.THREE)return;
/* ---------- silnik ---------- */
const cv=document.getElementById('c3');
const R=new THREE.WebGLRenderer({canvas:cv,antialias:true});
R.setPixelRatio(Math.min(devicePixelRatio,2));R.shadowMap.enabled=true;R.shadowMap.type=THREE.PCFSoftShadowMap;
R.outputEncoding=THREE.sRGBEncoding;R.toneMapping=THREE.ACESFilmicToneMapping;R.toneMappingExposure=1.05;
let scene,cam,upd=[],az=Math.PI/4,el=.62,zoom=1,auto=true;
function camSet(){const r=40;cam.position.set(Math.cos(az)*Math.cos(el)*r,Math.sin(el)*r,Math.sin(az)*Math.cos(el)*r);cam.lookAt(0,1,0);cam.zoom=zoom;cam.updateProjectionMatrix()}
function size(){const w=cv.clientWidth,h=cv.clientHeight;R.setSize(w,h,false);if(cam){const a=w/h,v=w<700?10:8.2;cam.left=-v*a;cam.right=v*a;cam.top=v;cam.bottom=-v;cam.updateProjectionMatrix()}}
addEventListener('resize',size);
{let d=null;cv.addEventListener('pointerdown',e=>{d={x:e.clientX,y:e.clientY,az,el};auto=false;cv.setPointerCapture(e.pointerId)});
 cv.addEventListener('pointermove',e=>{if(!d)return;az=d.az+(e.clientX-d.x)*.008;el=Math.max(.25,Math.min(1.2,d.el+(e.clientY-d.y)*.005));camSet()});
 cv.addEventListener('pointerup',()=>d=null);cv.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.6,Math.min(2.4,zoom*(e.deltaY<0?1.1:.9)));camSet()},{passive:false})}

const M=(c,o={})=>new THREE.MeshStandardMaterial({color:c,roughness:o.r??.75,metalness:o.m??0,emissive:o.e??0,emissiveIntensity:o.ei??1,transparent:o.t!=null,opacity:o.t??1});
function add(g,mesh,x,y,z,ry=0){mesh.position.set(x,y,z);mesh.rotation.y=ry;mesh.castShadow=true;mesh.receiveShadow=true;g.add(mesh);return mesh}
const B=(w,h,d)=>new THREE.BoxGeometry(w,h,d),CY=(a,b,h,s=20)=>new THREE.CylinderGeometry(a,b,h,s),SP=(r,s=20)=>new THREE.SphereGeometry(r,s,s);
function rbox(g,w,h,d,mat,x,y,z,ry){return add(g,new THREE.Mesh(B(w,h,d),mat),x,y+h/2,z,ry)}
function lights(sky,ground,sun,sunI,pos){scene.add(new THREE.HemisphereLight(sky,ground,.75));const s=new THREE.DirectionalLight(sun,sunI);s.position.set(...pos);s.castShadow=true;s.shadow.mapSize.set(2048,2048);
 const c=s.shadow.camera;c.left=c.bottom=-18;c.right=c.top=18;c.near=1;c.far=80;s.shadow.bias=-.0004;scene.add(s)}
function plight(c,i,d,x,y,z){const p=new THREE.PointLight(c,i,d,2);p.position.set(x,y,z);scene.add(p);return p}
// tekstura ekranu rysowana na płótnie (wykresy, miniaturki, napisy)
function screenTex(w,h,draw){const c=document.createElement('canvas');c.width=w;c.height=h;const x=c.getContext('2d');const t=new THREE.CanvasTexture(c);t.encoding=THREE.sRGBEncoding;t.userData={x,c,draw};draw(x,w,h,0);return t}
function screen(g,w,h,tex,x,y,z,ry){const m=new THREE.Mesh(new THREE.PlaneGeometry(w,h),new THREE.MeshBasicMaterial({map:tex,toneMapped:false}));m.position.set(x,y,z);m.rotation.y=ry||0;g.add(m);return m}
let CN=[];function candlesData(){if(CN.length)return CN;let p=100;for(let i=0;i<80;i++){const o=p,c=o+(Math.random()-.46)*3.2;p=c;CN.push({o,c,h:Math.max(o,c)+Math.random()*1.6,l:Math.min(o,c)-Math.random()*1.6})}return CN}
function drawCandles(x,w,h,t,bg='#06101f'){x.fillStyle=bg;x.fillRect(0,0,w,h);x.strokeStyle='rgba(110,140,255,.15)';for(let i=1;i<6;i++){x.beginPath();x.moveTo(0,h*i/6);x.lineTo(w,h*i/6);x.stroke()}
 const D=candlesData(),k=20+Math.floor(t*1.5)%60,S=D.slice(k-20,k+20),mn=Math.min(...S.map(d=>d.l)),mx=Math.max(...S.map(d=>d.h)),Y=v=>h*.08+(mx-v)/(mx-mn)*h*.84,bw=w/S.length;
 S.forEach((d,i)=>{const c=d.c>=d.o?'#26d07c':'#ff4d61';x.strokeStyle=c;x.fillStyle=c;x.lineWidth=Math.max(1,bw*.12);x.beginPath();x.moveTo(i*bw+bw/2,Y(d.h));x.lineTo(i*bw+bw/2,Y(d.l));x.stroke();x.fillRect(i*bw+bw*.18,Y(Math.max(d.o,d.c)),bw*.64,Math.max(2,Math.abs(Y(d.o)-Y(d.c))))})}
// postać agenta: tułów, głowa, ramiona; kiwa się przy pracy
function agent(g,col,x,z,ry,t0=0){const p=new THREE.Group();const body=M(col,{r:.55});
 add(p,new THREE.Mesh(CY(.26,.32,.75),body),0,.62,0);const head=add(p,new THREE.Mesh(SP(.22),M('#f0cdb0',{r:.6})),0,1.22,0);
 add(p,new THREE.Mesh(CY(.28,.28,.12),M('#2a2a35')),0,.2,0);add(p,new THREE.Mesh(CY(.05,.05,.4),M('#2a2a35')),0,.02,0);
 const aL=add(p,new THREE.Mesh(B(.1,.1,.45),body),-.3,.78,.2),aR=add(p,new THREE.Mesh(B(.1,.1,.45),body),.3,.78,.2);
 p.position.set(x,0,z);p.rotation.y=ry;g.add(p);upd.push(t=>{head.position.y=1.22+Math.sin(t*2+t0)*.02;head.rotation.y=Math.sin(t*.7+t0)*.35;aL.position.y=.78+Math.max(0,Math.sin(t*9+t0))*.04;aR.position.y=.78+Math.max(0,Math.sin(t*9+t0+1.7))*.04});return p}
function chair(g,x,z,ry){const p=new THREE.Group();const m=M('#23252f',{r:.5});add(p,new THREE.Mesh(B(.6,.08,.6),m),0,.5,0);add(p,new THREE.Mesh(B(.6,.7,.08),m),0,.9,-.28);add(p,new THREE.Mesh(CY(.04,.04,.5),M('#555')),0,.25,0);p.position.set(x,0,z);p.rotation.y=ry;g.add(p)}
function plant(g,x,z,s=1){add(g,new THREE.Mesh(CY(.28*s,.22*s,.5*s),M('#c9b8a3')),x,.25*s,z);for(let i=0;i<5;i++){const l=add(g,new THREE.Mesh(SP(.28*s,10),M(i%2?'#3f8a4a':'#2f6e3a')),x+Math.cos(i*1.3)*.18*s,(.65+i*.12)*s,z+Math.sin(i*1.3)*.18*s);l.scale.y=1.4}}
function deskWS(g,x,z,ry,scr,col,t0){const p=new THREE.Group();const wood=M('#8a6a4c',{r:.6});rbox(p,2.1,.08,1,wood,0,.92,0);[[-.95,-.4],[.95,-.4],[-.95,.4],[.95,.4]].forEach(([a,b])=>rbox(p,.07,.92,.07,M('#2b2b33'),a,0,b));
 rbox(p,1.3,.8,.06,M('#15161d',{r:.3}),0,1.12,-.35);screen(p,1.22,.72,scr,0,1.52,-.31);rbox(p,.5,.03,.18,M('#d7d7de'),0,1,.15);
 p.position.set(x,0,z);p.rotation.y=ry;g.add(p);chair(p,0,.85,Math.PI);const a=agent(p,col,0,.95,Math.PI,t0);return p}

const COLS=['#8f6bff','#ff6b9a','#1fc7a8','#ffb547','#5ab0ff','#c88bff','#7cd36b'];
/* ---------- wspólne dodatki ---------- */
function tag(g,text,x,y,z,col='#fff',w=2){const t=screenTex(256,64,(c,W,H)=>{c.fillStyle='rgba(8,10,18,.78)';c.fillRect(0,0,W,H);c.fillStyle=col;c.font='600 28px IBM Plex Sans, sans-serif';c.textAlign='center';c.fillText(text,W/2,42)});const s=new THREE.Sprite(new THREE.SpriteMaterial({map:t,depthWrite:false}));s.scale.set(w,w/4,1);s.position.set(x,y,z);g.add(s);return s}
function skyline(w,h,sky1,sky2,lit){return screenTex(w,h,(c,W,H)=>{const gr=c.createLinearGradient(0,0,0,H);gr.addColorStop(0,sky1);gr.addColorStop(1,sky2);c.fillStyle=gr;c.fillRect(0,0,W,H);let x=0,s=3;const r=()=>((s=s*9301+49297)%233280)/233280;
 while(x<W){const bw=W*(.04+r()*.06),bh=H*(.25+r()*.5);c.fillStyle='rgba(10,12,24,.92)';c.fillRect(x,H-bh,bw,bh);for(let yy=H-bh+6;yy<H-4;yy+=8)for(let xx=x+3;xx<x+bw-3;xx+=6)if(r()<lit){c.fillStyle=`rgba(255,${190+r()*50|0},120,.85)`;c.fillRect(xx,yy,3,4)}x+=bw+1}})}
function sofa(g,x,z,ry,col){const p=new THREE.Group();const m=M(col,{r:.9});rbox(p,2.4,.45,.9,m,0,0,0);rbox(p,2.4,.6,.25,m,0,.45,-.33);rbox(p,.25,.35,.9,m,-1.08,.45,0);rbox(p,.25,.35,.9,m,1.08,.45,0);p.position.set(x,0,z);p.rotation.y=ry;g.add(p)}
function pendant(g,x,z,h=6.4){add(g,new THREE.Mesh(CY(.01,.01,h-4.6),M('#111')),x,h-(h-4.6)/2,z);const sh=add(g,new THREE.Mesh(new THREE.ConeGeometry(.35,.35,20,1,true),M('#1b1b1f',{e:'#ffcf8a',ei:.15})),x,4.5,z);add(g,new THREE.Mesh(SP(.1),new THREE.MeshBasicMaterial({color:'#ffe2b0'})),x,4.35,z);plight('#ffcf8a',1.1,5,x,4.2,z)}

function timelineTex(){return screenTex(512,200,(x,w,h,t)=>{x.fillStyle='#0d0f15';x.fillRect(0,0,w,h);const cols=['#5ab0ff','#ff6b9a','#ffb547','#1fc7a8','#8f6bff'];
 for(let tr=0;tr<4;tr++){let px=10;for(let k=0;k<7;k++){const ww=30+((k*37+tr*53)%60);x.fillStyle=cols[(k+tr)%5];x.globalAlpha=.85;x.fillRect(px,20+tr*40,ww,30);x.globalAlpha=1;px+=ww+4}}
 const ph=(t*60)%w;x.fillStyle='#ff3b4e';x.fillRect(ph,8,2,h-16)})}
/* ---------- WARSZTAT TWÓRCY v2 ---------- */
function workshop(){scene.background=new THREE.Color('#0a0b12');scene.fog=new THREE.Fog('#0a0b12',45,95);lights('#cfc4ff','#0b0c14','#d9ccff',.8,[-9,12,7]);
 const g=new THREE.Group();scene.add(g);
 const wood=screenTex(512,512,(x,w,h)=>{for(let i=0;i<16;i++){x.fillStyle=`hsl(${24+Math.random()*6},${38+Math.random()*10}%,${28+Math.random()*8}%)`;x.fillRect(0,i*32,w,31);for(let k=0;k<6;k++){x.strokeStyle='rgba(0,0,0,.12)';x.beginPath();x.moveTo(Math.random()*w,i*32+Math.random()*30);x.lineTo(Math.random()*w,i*32+Math.random()*30);x.stroke()}x.fillStyle='rgba(0,0,0,.35)';x.fillRect(((i*137)%5)*100,i*32,2,31)}});
 wood.wrapS=wood.wrapT=THREE.RepeatWrapping;wood.repeat.set(3,2);rbox(g,16,.2,12,new THREE.MeshStandardMaterial({map:wood,roughness:.6}),0,-.2,0);
 const brick=screenTex(256,256,(x,w,h)=>{x.fillStyle='#2e2420';x.fillRect(0,0,w,h);for(let r=0;r<16;r++)for(let c=0;c<8;c++){x.fillStyle=`hsl(${14+Math.random()*10},${22+Math.random()*14}%,${26+Math.random()*10}%)`;x.fillRect(c*32+(r%2)*16+1,r*16+1,30,14)}});brick.wrapS=brick.wrapT=THREE.RepeatWrapping;brick.repeat.set(4,2);
 const wm=new THREE.MeshStandardMaterial({map:brick,roughness:.95});rbox(g,16,6,.3,wm,0,0,-6);rbox(g,.3,6,12,wm,-8,0,0);
 // okno z wieczornym światłem
 const sky=skyline(512,512,'#0e0b26','#3a2a7a',.15);screen(g,3.2,2.6,sky,-7.83,3.2,2.6,Math.PI/2);rbox(g,.12,2.8,3.4,M('#1c1c20',{m:.5}),-7.86,1.9,2.6);
 const sun=new THREE.SpotLight('#8f7bff',1.6,24,.5,.6);sun.position.set(-7.6,3.4,2.6);sun.target.position.set(1,0,2);sun.castShadow=true;scene.add(sun,sun.target);
 // neon nad biurkiem
 const neon=screenTex(512,128,(x,w,h,t)=>{x.clearRect(0,0,w,h);x.shadowColor='#8f7bff';x.shadowBlur=28;x.fillStyle=Math.sin(t*7)>-.92?'#e6e0ff':'#3a2f6a';x.font='bold 84px Bricolage Grotesque, sans-serif';x.textAlign='center';x.fillText('MONTAŻ',w/2,92)});
 const nm=new THREE.Mesh(new THREE.PlaneGeometry(4,1),new THREE.MeshBasicMaterial({map:neon,transparent:true,toneMapped:false}));nm.position.set(0,4.6,-5.82);scene.add(nm);upd.push(t=>{neon.userData.draw(neon.userData.x,512,128,t);neon.needsUpdate=true});plight('#8f7bff',2.4,7,0,4.4,-5);
 // panele akustyczne
 for(let i=0;i<4;i++)for(let j=0;j<2;j++)rbox(g,.9,.9,.1,M(['#3a2f4a','#4a2f3a'][(i+j)%2],{r:1}),-5.2+i*1.05+(i>1?7.2:0),2.6+j*1.0,-5.82);
 // biurko z dwoma dużymi monitorami i laptopem
 const desk=new THREE.Group();desk.position.set(0,0,-4.4);g.add(desk);rbox(desk,4.6,.12,1.5,M('#6b4a30',{r:.45}),0,.9,0);
 for(const [a,b] of [[-2.1,-.6],[2.1,-.6],[-2.1,.6],[2.1,.6]])rbox(desk,.12,.9,.12,M('#1c1c20',{m:.6}),a,0,b);
 const tl=timelineTex(),pv=screenTex(320,180,(x,w,h,t)=>{const gr=x.createLinearGradient(0,0,w,h);gr.addColorStop(0,`hsl(${(t*25)%360},60%,50%)`);gr.addColorStop(1,`hsl(${(t*25+70)%360},60%,28%)`);x.fillStyle=gr;x.fillRect(0,0,w,h);x.fillStyle='#fff';x.font='bold 26px Bricolage Grotesque, sans-serif';x.font='bold 22px Geist, system-ui, sans-serif';x.fillText((window.WS_STATUS||'Montażownia').slice(0,26),18,h-20)});
 [[-1.05,.18,tl],[1.05,-.18,pv]].forEach(([x,r,tx])=>{rbox(desk,2,1.15,.06,M('#0b0b0e',{m:.4}),x,1.25,-.45,r);screen(desk,1.9,1.06,tx,x,1.82,-.41,r);rbox(desk,.08,.35,.08,M('#222'),x,.98,-.4)});
 rbox(desk,1,.04,.65,M('#c7cbd4',{m:.8,r:.3}),-1.4,1.0,.3);rbox(desk,1,.6,.04,M('#c7cbd4',{m:.8,r:.3}),-1.4,1.02,.02,-.2);rbox(desk,.9,.03,.3,M('#2a2a2e'),.4,1.0,.3);
 const mug=add(desk,new THREE.Mesh(CY(.09,.08,.2,16),M('#e8e4dc')),1.8,1.06,.3);
 const ch=new THREE.Group();rbox(ch,.8,.14,.8,M('#232327',{r:.5}),0,.5,0);rbox(ch,.75,1,.14,M('#232327',{r:.5}),0,.75,.36);rbox(ch,.08,.5,.08,M('#888',{m:.7}),0,0,0);ch.position.set(0,0,-3.1);g.add(ch);
 agent(g,'#ff6b9a',0,-3.0,Math.PI,1);tag(g,'Montażysta',0,2.7,-3.0,'#ff6b9a',1.8);
 // regał z kamerami i obiektywami
 const sh=new THREE.Group();sh.position.set(-7.4,0,-2.4);sh.rotation.y=Math.PI/2;g.add(sh);rbox(sh,3,3.2,.5,M('#4a3524',{r:.6}),0,0,0);
 for(let r=0;r<3;r++)for(let k=0;k<4;k++){const x=-1.1+k*.72,y=.55+r*1;if((r+k)%3===0){rbox(sh,.42,.3,.3,M('#1a1a1e',{m:.4}),x,y,.05);add(sh,new THREE.Mesh(CY(.1,.1,.18,16),M('#0c0c0e',{m:.6})),x,y+.15,.27).rotation.x=Math.PI/2}else if((r+k)%3===1){add(sh,new THREE.Mesh(CY(.11,.11,.32,16),M('#202024',{m:.5})),x,y+.16,.05)}else{const re=add(sh,new THREE.Mesh(CY(.22,.22,.06,24),M('#9aa0ab',{m:.8})),x,y+.24,.05);re.rotation.x=Math.PI/2}}
 // strefa nagrań: ring light + statyw + tło
 const ring=add(g,new THREE.Mesh(new THREE.TorusGeometry(.7,.07,12,48),new THREE.MeshBasicMaterial({color:'#3a3b4a'})),5,2.4,1.4);add(g,new THREE.Mesh(CY(.03,.03,2.4),M('#222')),5,1.1,1.4);const rl=plight('#c4b8ff',0,6,5,2.4,2);
 // ring light świeci, gdy trwa montaż (window.WS_BUSY ustawia Montażownia)
 upd.push(t=>{const on=!!window.WS_BUSY;ring.material.color.set(on?(Math.sin(t*3)>-.6?'#d9d0ff':'#a898ff'):'#3a3b4a');rl.intensity=on?2.2:0});
 const tri=new THREE.Group();tri.position.set(4,0,3);g.add(tri);for(let k=0;k<3;k++){const l=add(tri,new THREE.Mesh(CY(.025,.025,1.7),M('#333')),Math.cos(k*2.1)*.3,.8,Math.sin(k*2.1)*.3);l.rotation.z=Math.cos(k*2.1)*.18;l.rotation.x=-Math.sin(k*2.1)*.18}rbox(tri,.35,.3,.6,M('#111',{m:.3}),0,1.6,0);
 rbox(g,3,3.2,.05,M('#2c4a3a',{r:1}),5.6,0,-1);
 // kącik strażnika: dywan, fotel, lampa, roślina
 const rug=add(g,new THREE.Mesh(CY(1.8,1.8,.03,40),M('#7a3b2c',{r:1})),-3.6,.02,3.2);rug.castShadow=false;
 sofa(g,-4,4.4,Math.PI,'#8a5a34');agent(g,'#5ab0ff',-4,4.5,Math.PI,3).position.y=.15;tag(g,'Strażnik montażu',-4,2.7,4.4,'#5ab0ff',2.1);
 rbox(g,1.1,.4,.7,M('#3a2a1e'),-3.4,0,2.8);add(g,new THREE.Mesh(CY(.02,.02,2),M('#222')),-6.2,1,4.4);add(g,new THREE.Mesh(new THREE.ConeGeometry(.35,.4,20,1,true),M('#222',{e:'#ffcf8a',ei:.4})),-6.2,2.1,4.4);plight('#b9a8ff',1.0,5,-6.2,1.9,4.4);
 plant(g,7.2,-5,1.4);plant(g,7.2,5,1.2);
 for(let i=0;i<3;i++){add(g,new THREE.Mesh(CY(.005,.005,1.2),M('#111')),-3+i*3,5.4,-1);add(g,new THREE.Mesh(SP(.13,12),new THREE.MeshBasicMaterial({color:'#d9d0ff'})),-3+i*3,4.8,-1);plight('#a898ff',.9,6,-3+i*3,4.6,-1)}
 upd.push(t=>{if(Math.floor(t*12)%2===0){tl.userData.draw(tl.userData.x,512,200,t);
  // pasek postępu aktualnego montażu na monitorze z osią czasu
  if(window.WS_BUSY){const x=tl.userData.x,p=Math.max(.04,Math.min(1,window.WS_PROG||0));x.fillStyle='rgba(10,11,18,.85)';x.fillRect(0,168,512,32);x.fillStyle='#8f7bff';x.fillRect(8,180,(512-16)*p,8);x.fillStyle='#e6e0ff';x.font='600 13px Geist,system-ui';x.fillText(String(window.WS_STATUS||'').slice(0,48),8,176)}
  tl.needsUpdate=true;pv.userData.draw(pv.userData.x,320,180,t);pv.needsUpdate=true}})}

scene=new THREE.Scene();cam=new THREE.OrthographicCamera(-1,1,1,-1,.1,200);size();camSet();workshop();
window.WS_STATUS=window.WS_STATUS||'';
const clock=new THREE.Clock();
(function loop(){const t=clock.getElapsedTime();if(window.CENTRUM_ACTIVE!==false&&!document.hidden){if(auto){az+=.0012;camSet()}upd.forEach(f=>f(t));R.render(scene,cam)}requestAnimationFrame(loop)})();
})();
