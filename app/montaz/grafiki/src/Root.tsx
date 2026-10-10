import React from 'react';
import {AbsoluteFill, Composition, Img, staticFile, useCurrentFrame, useVideoConfig, spring, interpolate, Easing} from 'remotion';

// Animowane grafiki do daily filmu jedzeniowego. Każda renderuje się jako przezroczysty klip (ProRes 4444)
// i jest nakładana przez ffmpeg w montuj.py w miejsce statycznego PNG.

type Size = {width: number; height: number; seconds: number};
type TytulProps = Size & {lines: string[]; size: number; top: number; font: string};
type ObrazProps = Size & {src: string; style: 'karta' | 'naklejka'; imgW: number; imgH: number};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// tytuł: linijki wjeżdżają po kolei od dołu, na końcu znikają
const Tytul: React.FC<TytulProps> = ({lines, size, top, font}) => {
  const f = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const out = interpolate(f, [durationInFrames - 9, durationInFrames - 1], [1, 0], clamp);
  return (
    <AbsoluteFill style={{opacity: out}}>
      <style>{`@font-face{font-family:T;src:url(${staticFile(font)});font-weight:100 900}`}</style>
      <div style={{position: 'absolute', top, width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6}}>
        {lines.map((l, i) => {
          const s = spring({frame: f - 3 - i * 5, fps, config: {damping: 14, stiffness: 160}});
          return (
            <div key={i} style={{
              fontFamily: 'T', fontWeight: 650, fontSize: size, lineHeight: 1.2, color: '#fff', textAlign: 'center',
              WebkitTextStroke: '3px rgb(20,20,20)', paintOrder: 'stroke fill',
              textShadow: '0 0 14px rgba(0,0,0,0.75), 0 4px 10px rgba(0,0,0,0.6)',
              opacity: Math.min(1, s * 1.4), transform: `translateY(${(1 - s) * 50}px) scale(${0.9 + 0.1 * s})`,
            }}>{l}</div>
          );
        })}
      </div>
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
  </>
);
