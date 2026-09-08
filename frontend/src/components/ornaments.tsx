/* =============================================================================
   Ornaments.tsx — every drawn mark in the Wayfarer Field Journal system.

   Rules these follow, and that any new mark must follow:
     · stroked SVG on a 16 or 24px grid, stroke-width 1.1–1.2 at native size
     · fill: none except where a mark is genuinely solid (the seal, die pips)
     · colour comes from `currentColor` — never hard-code a hex here
     · aria-hidden; the meaning lives in adjacent text, never in the glyph
     · no emoji, ever

   Plain JS/JSX: delete the type annotations and the `.tsx` extension.
   ========================================================================== */

type MarkProps = { size?: number; className?: string };

const base = (size: number, box: number, className?: string) => ({
  width: size,
  height: size,
  viewBox: `0 0 ${box} ${box}`,
  fill: "none" as const,
  stroke: "currentColor",
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  focusable: false,
  className,
});

/* --- the brand mark. Topbar only; never decorative. ---------------------- */
export function Compass({ size = 26, className }: MarkProps) {
  return (
    <svg {...base(size, 24, className)} strokeWidth={1.1}>
      <circle cx="12" cy="12" r="9.2" />
      <circle cx="12" cy="12" r="5.4" strokeDasharray="1 2.2" />
      <path d="M12 1.6 13.9 10.1 12 12 10.1 10.1Z" />
      <path d="M12 22.4 10.1 13.9 12 12 13.9 13.9Z" />
      <path d="M22.4 12 13.9 13.9 12 12 13.9 10.1Z" />
      <path d="M1.6 12 10.1 10.1 12 12 10.1 13.9Z" />
    </svg>
  );
}

/* --- a declared action. Marks .player-message and the commit button. ------ */
export function Nib({ size = 16, className }: MarkProps) {
  return (
    <svg {...base(size, 16, className)} strokeWidth={1.15}>
      <path d="M8 1.4 12.3 8.2 8 14.6 3.7 8.2Z" />
      <path d="M8 6.2V14.6" />
      <circle cx="8" cy="8.4" r="1.15" />
    </svg>
  );
}

/* --- a seat. Lit is whose turn it is; unlit is everyone else. ------------- */
export function Lamp({
  size = 16,
  lit = false,
  className,
}: MarkProps & { lit?: boolean }) {
  if (!lit) {
    return (
      <svg {...base(size, 16, className)} strokeWidth={1.15}>
        <path d="M8 2.2c1.9 1.7 2.9 3.2 2.9 4.7A2.9 2.9 0 0 1 8 9.8 2.9 2.9 0 0 1 5.1 6.9c0-1.5 1-3 2.9-4.7Z" />
        <path d="M6 12.4h4M6.7 14.2h2.6" />
      </svg>
    );
  }
  return (
    <svg {...base(size, 16, className)} strokeWidth={1.2}>
      <path
        d="M8 2.6c1.8 1.6 2.7 3 2.7 4.4A2.7 2.7 0 0 1 8 9.7 2.7 2.7 0 0 1 5.3 7c0-1.4.9-2.8 2.7-4.4Z"
        fill="currentColor"
        fillOpacity=".2"
      />
      <path d="M6.1 12.3h3.8M6.8 14h2.4" />
      <path
        d="M13.4 4.1 14.7 3M2.6 4.1 1.3 3M14.6 7.6h1.6M-.2 7.6h1.6"
        opacity=".65"
      />
    </svg>
  );
}

/* --- the campaign seal. One per campaign, on the rail. Never decorative. ---
   This is the one mark that is filled, because wax is. It reads --wax and
   --page directly, so it is the single exception to the currentColor rule. */
export function Seal({ size = 46, className }: MarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      aria-hidden
      focusable={false}
      className={className}
    >
      <path
        d="M24 2.6c5.4-1.4 9.6 2.2 13.4 4.4 4 2.3 7.8 3.6 8.6 8.4.8 4.7-2 8.3-2 12.6s2.9 8.2 1.5 12.4c-1.4 4.3-6.4 4.6-10.3 6.3-3.9 1.7-7 4.5-11.2 3.9-4.2-.6-6.7-4.4-10.3-6.6C10 41.8 5 41.3 3 37.3c-2-4 .6-8.2.6-12.7 0-4.5-2.4-8.9-.2-12.7C5.7 8 10.8 8 14.8 5.9 18 4.2 20.7 3.4 24 2.6Z"
        fill="var(--wax)"
        fillOpacity=".9"
      />
      <g
        stroke="var(--page)"
        strokeWidth="1.2"
        fill="none"
        strokeOpacity=".78"
        strokeLinejoin="round"
      >
        <circle cx="24" cy="24" r="12.4" />
        <circle cx="24" cy="24" r="7.4" strokeDasharray="1.2 2.6" />
        <path d="M24 9.8 26.6 22 24 24 21.4 22Z" />
        <path d="M24 38.2 21.4 26 24 24 26.6 26Z" />
        <path d="M38.2 24 26 26.6 24 24 26 21.4Z" />
        <path d="M9.8 24 22 21.4 24 24 22 26.6Z" />
      </g>
    </svg>
  );
}

/* --- a break in the scene, in place of a horizontal rule. ---------------- */
export function Fleuron({
  width = 132,
  className,
}: {
  width?: number;
  className?: string;
}) {
  return (
    <svg
      width={width}
      height={16}
      viewBox="0 0 132 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.1}
      strokeLinecap="round"
      aria-hidden
      focusable={false}
      className={className}
    >
      <path d="M2 8h38" />
      <path d="M92 8h38" />
      <path d="M52 8c3-4.4 7-4.4 9.6-1.4 1.6 1.8 2.6 3.4 4.4 3.4s2.8-1.6 4.4-3.4C73 3.6 77 3.6 80 8" />
      <path d="M52 8c3 4.4 7 4.4 9.6 1.4C63.2 7.6 64.2 6 66 6s2.8 1.6 4.4 3.4C73 12.4 77 12.4 80 8" />
      <circle cx="45.6" cy="8" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="86.4" cy="8" r="1.5" fill="currentColor" stroke="none" />
    </svg>
  );
}

/* --- dice -----------------------------------------------------------------
   One <Die> per rolled face. Each carries a fixed tilt by index, so a given
   roll always renders identically — the tilt is decoration, not entropy, and
   must not change between renders of the same result.                        */
const PIPS: Record<number, [number, number][]> = {
  1: [[11, 11]],
  2: [
    [6.6, 6.6],
    [15.4, 15.4],
  ],
  3: [
    [6.6, 6.6],
    [11, 11],
    [15.4, 15.4],
  ],
  4: [
    [6.6, 6.6],
    [15.4, 6.6],
    [6.6, 15.4],
    [15.4, 15.4],
  ],
  5: [
    [6.6, 6.6],
    [15.4, 6.6],
    [11, 11],
    [6.6, 15.4],
    [15.4, 15.4],
  ],
  6: [
    [6.6, 6.6],
    [15.4, 6.6],
    [6.6, 11],
    [15.4, 11],
    [6.6, 15.4],
    [15.4, 15.4],
  ],
};
const TILT = [-7, 4, -3, 8, -5, 6];

export function Die({
  face,
  size = 22,
  index = 0,
}: {
  face: number;
  size?: number;
  index?: number;
}) {
  const pips = PIPS[face];
  if (!pips) return null;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 22 22"
      fill="none"
      aria-hidden
      focusable={false}
      style={{ transform: `rotate(${TILT[index % TILT.length]}deg)` }}
    >
      <rect
        x="1.1"
        y="1.1"
        width="19.8"
        height="19.8"
        rx="3.4"
        stroke="currentColor"
        strokeWidth="1.15"
      />
      {pips.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="1.55" fill="currentColor" />
      ))}
    </svg>
  );
}

/* The whole roll. The accessible name lives here, not on the individual dice. */
export function Dice({ faces, size = 22 }: { faces: number[]; size?: number }) {
  return (
    <span
      className="dice"
      role="img"
      aria-label={`${faces.length}d6: ${faces.join(", ")}, total ${faces.reduce((a, b) => a + b, 0)}`}
    >
      {faces.map((f, i) => (
        <Die key={i} face={f} size={size} index={i} />
      ))}
    </span>
  );
}

/* --- a hand-drawn ring, for circling one thing on the page ----------------
   Absolutely positioned over its parent; the parent needs position:relative
   (see .turn-mark). Use once per screen at most.                            */
export function Ring({
  width = 96,
  height = 34,
}: {
  width?: number;
  height?: number;
}) {
  const w = width,
    h = height;
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      aria-hidden
      focusable={false}
    >
      <path
        d={`M${w * 0.5} 2.5C${w * 0.86} 2.2 ${w - 2.5} ${h * 0.24} ${w - 3} ${h * 0.5}c.4 ${h * 0.3}-${w * 0.16} ${h * 0.46}-${w * 0.5} ${h * 0.47}C${w * 0.16} ${h - 2} 2.5 ${h * 0.76} 3 ${h * 0.48} 3.4 ${h * 0.2} ${w * 0.2} 3 ${w * 0.5} 2.5Z`}
        opacity=".85"
      />
      <path
        d={`M${w * 0.62} 2.9c${w * 0.2} .6 ${w * 0.3} ${h * 0.2} ${w * 0.3} ${h * 0.36}`}
        opacity=".5"
      />
    </svg>
  );
}

/* --- pinned sketches ------------------------------------------------------
   These are a player's drawings. They live in the party margin, they are
   captioned in the hand, and they never carry information the system needs to
   assert — a sketch may be wrong, and that is the point. Add more here as the
   scenarios need them; keep the same stroke weight and the ground line.      */
export function SketchDoor({
  width = 170,
  className,
}: {
  width?: number;
  className?: string;
}) {
  return (
    <svg
      width={width}
      height={132}
      viewBox="0 0 170 132"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.15}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable={false}
      className={className}
    >
      <path d="M20 118V26c0-2 1.4-3.4 3.4-3.6L86 14v106Z" opacity=".9" />
      <path d="M86 14 128 24v86l-42 8" />
      <path d="M128 24v86" opacity=".5" />
      <path d="M78 68c2.4.4 3.6 1.6 3.6 3" />
      <g opacity=".5">
        <path d="M92 30 122 37M92 42 122 49M92 54 122 61M92 66 122 73" />
      </g>
      <path d="M12 118h146" />
      <g opacity=".75">
        <path d="M104 108c-1.6-5 .4-9 4-9.6 3.2-.6 5.6 1.4 6 4.4.4 3.4-1.6 5.6-4.4 6.2-2.4.6-4.8-.2-5.6-1Z" />
        <path d="M113 96.6c-.8-2.6.4-4.4 2.2-4.6 1.7-.2 2.8.9 3 2.4" />
        <path d="M119 93.4c-.6-1.9.3-3.2 1.6-3.4 1.2-.1 2 .7 2.2 1.8" />
      </g>
      <path d="M40 128c8-3 22-4 34-3.6" opacity=".45" strokeDasharray="1 4" />
    </svg>
  );
}

export function SketchKit({
  width = 200,
  className,
}: {
  width?: number;
  className?: string;
}) {
  return (
    <svg
      width={width}
      height={96}
      viewBox="0 0 200 96"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.15}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable={false}
      className={className}
    >
      <path d="M8 62 44 26c2-2 5-2 6.6 0l2.4 2.6c1.6 1.8 1.4 4.4-.4 6L16 70Z" />
      <path d="M14 68 8 74l-4-2 2-4Z" />
      <path d="M84 24h26M97 24v8" />
      <path d="M84 32h26l6 30c.4 2-1 3.6-3 3.6H81c-2 0-3.4-1.6-3-3.6Z" />
      <path d="M88 40h18l4 20H84Z" opacity=".45" />
      <path d="M97 44v12" opacity=".55" />
      <g opacity=".9">
        <path d="M144 20c8 4 12 10 8 16-3.4 5-11 4-13 9-1.8 4.6 4 8 9 6" />
        <path d="M148 60c-9 3-13 9-9 15 3 4.6 11 4 14 8" />
        <path d="M186 26c-6 6-4 12 1 15 4 2.4 8 6 5 11" />
      </g>
      <path d="M4 88h192" opacity=".35" strokeDasharray="1 5" />
    </svg>
  );
}
