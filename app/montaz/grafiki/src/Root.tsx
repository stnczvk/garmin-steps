import React from 'react';
import {AbsoluteFill, Composition, Img, staticFile, useCurrentFrame, useVideoConfig, spring, interpolate, Easing} from 'remotion';

// Animowane grafiki do daily filmu jedzeniowego. Każda renderuje się jako przezroczysty klip (ProRes 4444)
// i jest nakładana przez ffmpeg w montuj.py w miejsce statycznego PNG.

type Size = {width: number; height: number; seconds: number};
type TytulProps = Size & {lines: string[]; size: number; top: number; font: string};
type ObrazProps = Size & {src: string; style: 'karta' | 'naklejka'; imgW: number; imgH: number};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

const YEL = 'rgb(255,214,0)';

// tytuł: linijki na białych / żółtych paskach (jak w przepisach), wskakują po kolei, lekko przechylone
const Tytul: React.FC<TytulProps> = ({lines, size, top, font}) => {
  const f = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const out = interpolate(f, [durationInFrames - 8, durationInFrames - 1], [1, 0], clamp);
  const shade = interpolate(f, [0, 8], [0, 1], clamp) * out;
  return (
    <AbsoluteFill>
      <style>{`@font-face{font-family:T;src:url(${staticFile(font)});font-weight:100 900}`}</style>
      <AbsoluteFill style={{background: 'linear-gradient(180deg, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0.18) 60%, rgba(0,0,0,0) 100%)', opacity: shade}} />
      <div style={{position: 'absolute', top, width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16}}>
        {lines.map((l, i) => {
          const s = spring({frame: f - 2 - i * 5, fps, config: {damping: 11, stiffness: 170, mass: 0.8}});
          const kcal = /kcal/i.test(l);
          const big = i === 0;
          const rot = (i % 2 ? 1.6 : -1.6) * (1 - s) + (i % 2 ? 1 : -1.2);
          const o = interpolate(f, [durationInFrames - 9 + i, durationInFrames - 2 + i], [1, 0], clamp);
          return (
            <div key={i} style={{
              fontFamily: 'T', fontWeight: 800, fontSize: big ? size : Math.round(size * 0.78), lineHeight: 1.12,
              color: '#111', background: kcal ? YEL : '#fff', padding: big ? '12px 34px 16px' : '8px 28px 12px',
              borderRadius: 18, whiteSpace: 'nowrap', boxShadow: '0 16px 44px rgba(0,0,0,0.45)',
              opacity: Math.min(1, s * 1.6) * o, transform: `scale(${(0.55 + 0.45 * s) * (0.9 + 0.1 * o)}) rotate(${rot}deg)`,
            }}>{l}</div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// podsumowanie dnia na końcu: kalorie i makro z dziennika (odliczanie + paski względem celu)
type Cel = number | number[] | null;
type PodsProps = Size & {font: string; label: string; kcal: number; goal: number | null; p: number; c: number; fat: number;
  gp: Cel; gc: Cel; gf: Cel; train: boolean; cta: string};
const top = (g: Cel) => (g == null ? null : Array.isArray(g) ? g[g.length - 1] : g);
const fmtCel = (g: Cel) => (g == null ? '' : Array.isArray(g) ? g.join('–') : String(g));

const Licz: React.FC<{v: number; start: number; style: React.CSSProperties}> = ({v, start, style}) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [start, start + 26], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  return <span style={style}>{Math.round(v * p)}</span>;
};

const Podsumowanie: React.FC<PodsProps> = ({font, label, kcal, goal, p, c, fat, gp, gc, gf, train, cta}) => {
  const f = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const sp = (s: number, d = 14) => spring({frame: f - s, fps, config: {damping: d, stiffness: 150}});
  const out = interpolate(f, [durationInFrames - 7, durationInFrames - 1], [1, 0], clamp);
  const bg = interpolate(f, [0, 8], [0, 1], clamp) * out;
  const fill = (v: number, g: Cel, st: number) => {
    const t = top(g);
    return t ? Math.min(1, v / t) * interpolate(f, [st, st + 26], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)}) : 0;
  };
  const tiles: [string, number, Cel][] = [['BIAŁKO', p, gp], ['WĘGLE', c, gc], ['TŁUSZCZ', fat, gf]];
  const kc = sp(8, 10);
  const ctaS = sp(34, 11);
  return (
    <AbsoluteFill>
      <style>{`@font-face{font-family:T;src:url(${staticFile(font)});font-weight:100 900}`}</style>
      <AbsoluteFill style={{background: 'rgba(8,8,10,0.62)', opacity: bg}} />
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', padding: '0 70px', gap: 24, opacity: out, fontFamily: 'T'}}>
        <div style={{fontWeight: 700, fontSize: 32, letterSpacing: 9, color: 'rgba(255,255,255,0.75)', opacity: sp(2), transform: `translateY(${(1 - sp(2)) * 30}px)`}}>
          PODSUMOWANIE DNIA</div>
        <div style={{display: 'flex', gap: 14, opacity: sp(4), transform: `translateY(${(1 - sp(4)) * 30}px)`}}>
          <div style={{background: '#fff', color: '#111', fontWeight: 800, fontSize: 44, padding: '6px 24px 10px', borderRadius: 14}}>{label}</div>
          <div style={{background: train ? YEL : 'rgba(255,255,255,0.16)', color: train ? '#111' : '#fff', fontWeight: 700, fontSize: 34,
            padding: '12px 22px', borderRadius: 14}}>{train ? 'TRENING ✓' : 'BEZ TRENINGU'}</div>
        </div>
        <div style={{display: 'flex', alignItems: 'baseline', gap: 16, marginTop: 10, transform: `scale(${0.7 + 0.3 * kc})`, opacity: Math.min(1, kc * 1.5)}}>
          <Licz v={kcal} start={10} style={{fontWeight: 900, fontSize: 220, color: YEL, lineHeight: 1}} />
          <span style={{fontWeight: 700, fontSize: 58, color: 'rgba(255,255,255,0.92)'}}>kcal</span>
        </div>
        {goal ? (
          <div style={{width: '100%', opacity: sp(12)}}>
            <div style={{height: 18, borderRadius: 9, background: 'rgba(255,255,255,0.16)', overflow: 'hidden'}}>
              <div style={{height: '100%', width: `${fill(kcal, goal, 12) * 100}%`, background: YEL, borderRadius: 9}} />
            </div>
            <div style={{marginTop: 12, fontWeight: 600, fontSize: 30, color: 'rgba(255,255,255,0.72)', textAlign: 'center'}}>cel {goal} kcal</div>
          </div>
        ) : null}
        <div style={{display: 'flex', gap: 20, width: '100%', marginTop: 12}}>
          {tiles.map(([l, v, g], i) => {
            const s = sp(18 + i * 4);
            return (
              <div key={l} style={{flex: 1, borderRadius: 28, background: 'rgba(16,16,18,0.78)', border: '2px solid rgba(255,255,255,0.14)',
                padding: '24px 22px', opacity: s, transform: `translateY(${(1 - s) * 50}px)`}}>
                <div style={{fontWeight: 700, fontSize: 23, letterSpacing: 3, color: 'rgba(255,255,255,0.66)'}}>{l}</div>
                <div style={{display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 8}}>
                  <Licz v={v} start={20 + i * 4} style={{fontWeight: 900, fontSize: 78, color: '#fff', lineHeight: 1}} />
                  <span style={{fontWeight: 700, fontSize: 30, color: 'rgba(255,255,255,0.85)'}}>g</span>
                </div>
                {top(g) ? (
                  <>
                    <div style={{height: 10, borderRadius: 5, background: 'rgba(255,255,255,0.14)', marginTop: 14, overflow: 'hidden'}}>
                      <div style={{height: '100%', width: `${fill(v, g, 20 + i * 4) * 100}%`, background: YEL}} />
                    </div>
                    <div style={{fontWeight: 600, fontSize: 22, color: 'rgba(255,255,255,0.6)', marginTop: 8}}>cel {fmtCel(g)} g</div>
                  </>
                ) : null}
              </div>
            );
          })}
        </div>
        <div style={{marginTop: 26, background: YEL, color: '#111', fontWeight: 800, fontSize: 38, padding: '16px 36px', borderRadius: 999,
          opacity: ctaS, transform: `scale(${0.8 + 0.2 * ctaS})`}}>{cta}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// karta posiłku / naklejka: „wskakuje” ze sprężyną, lekko rośnie, na końcu znika
const Obraz: React.FC<ObrazProps> = ({src, style, imgW, imgH}) => {
  const f = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const karta = style === 'karta';
  const s = spring({frame: f, fps, config: karta ? {damping: 13, stiffness: 170} : {damping: 8, stiffness: 220}});
  const out = interpolate(f, [durationInFrames - 8, durationInFrames - 1], [1, 0], {...clamp, easing: Easing.in(Easing.quad)});
  const drift = interpolate(f, [0, durationInFrames], [0, karta ? 0.03 : 0], clamp);
  const scale = (karta ? 0.75 + 0.25 * s : 0.4 + 0.6 * s) + drift - (1 - out) * 0.08;
  const rot = karta ? 0 : (1 - s) * -8;
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <Img src={staticFile(src)} style={{
        width: imgW, height: imgH,
        opacity: Math.min(1, s * 2) * out,
        transform: `translateY(${karta ? (1 - s) * 80 : 0}px) scale(${scale}) rotate(${rot}deg)`,
      }} />
    </AbsoluteFill>
  );
};

// karta końcowa: zapowiedź jutrzejszego dnia + obserwuj
type KoniecProps = Size & {font: string; next: number | null};
const Koniec: React.FC<KoniecProps> = ({font, next}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sp = (s: number, d = 12) => spring({frame: f - s, fps, config: {damping: d, stiffness: 160}});
  const a = sp(0), b = sp(7), c = sp(14, 10);
  const pulse = 1 + 0.04 * Math.sin(Math.max(0, f - 24) / 4);
  return (
    <AbsoluteFill style={{fontFamily: 'T'}}>
      <style>{`@font-face{font-family:T;src:url(${staticFile(font)});font-weight:100 900}`}</style>
      <AbsoluteFill style={{background: 'rgba(8,8,10,0.6)', opacity: interpolate(f, [0, 8], [0, 1], clamp)}} />
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', gap: 22}}>
        <div style={{fontWeight: 700, fontSize: 36, letterSpacing: 10, color: 'rgba(255,255,255,0.8)', opacity: a,
          transform: `translateY(${(1 - a) * 30}px)`}}>JUTRO</div>
        <div style={{background: '#fff', color: '#111', fontWeight: 900, fontSize: 100, padding: '10px 42px 16px', borderRadius: 22,
          opacity: Math.min(1, b * 1.6), transform: `scale(${0.55 + 0.45 * b}) rotate(${-1.5 * b}deg)`}}>
          {next ? `Dzień ${next}` : 'Kolejny dzień'}</div>
        <div style={{marginTop: 30, background: YEL, color: '#111', fontWeight: 800, fontSize: 44, padding: '18px 42px', borderRadius: 999,
          opacity: c, transform: `scale(${(0.8 + 0.2 * c) * pulse})`}}>Obserwuj, żeby nie przegapić</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const meta = ({props}: {props: Size}) => ({
  width: props.width, height: props.height, durationInFrames: Math.max(10, Math.round(props.seconds * 30)),
});

export const Root: React.FC = () => (
  <>
    <Composition id="Tytul" component={Tytul as any} width={1080} height={600} fps={30} durationInFrames={105}
      defaultProps={{width: 1080, height: 600, seconds: 3.5, lines: ['Redukcja dzień 33', '2100 kcal'], size: 78, top: 150, font: 'Montserrat.ttf'}}
      calculateMetadata={meta as any} />
    <Composition id="Obraz" component={Obraz as any} width={800} height={1200} fps={30} durationInFrames={90}
      defaultProps={{width: 800, height: 1200, imgW: 700, imgH: 1100, seconds: 3, src: 'karta.png', style: 'karta'}}
      calculateMetadata={meta as any} />
    <Composition id="Koniec" component={Koniec as any} width={1080} height={1920} fps={30} durationInFrames={60}
      defaultProps={{width: 1080, height: 1920, seconds: 2, font: 'Montserrat.ttf', next: 34}} calculateMetadata={meta as any} />
    <Composition id="Podsumowanie" component={Podsumowanie as any} width={1080} height={1920} fps={30} durationInFrames={120}
      defaultProps={{width: 1080, height: 1920, seconds: 4, font: 'Montserrat.ttf', label: 'DZIEŃ 33', kcal: 2087, goal: 2100,
        p: 162, c: 220, fat: 61, gp: [150, 170], gc: 230, gf: 65, train: true, cta: 'Obserwuj – jutro kolejny dzień'}}
      calculateMetadata={meta as any} />
  </>
);
