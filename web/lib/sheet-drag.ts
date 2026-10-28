// The physics of a bottom sheet the finger pulls down. Pure, so the release decision is tested
// without a DOM.

/** Only the samples this recent count towards the release velocity. */
const VELOCITY_WINDOW_MS = 100;

/**
 * Where a flick comes to rest: Apple's projection from "Designing Fluid Interfaces" (WWDC 2018),
 * an exponential decay. 0.99 is its snappy end, so a short flick does not carry the sheet far.
 */
function project(velocity: number, decelerationRate = 0.99): number {
  return ((velocity / 1000) * decelerationRate) / (1 - decelerationRate);
}

/** Resistance past the open position: the sheet follows less the further it is pulled up. */
function rubberband(overshoot: number, dimension: number, constant = 0.55): number {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

/** The offset a drag puts the sheet at: 1:1 downwards, rubber-banded upwards. */
export function dragOffset(raw: number, height: number): number {
  return raw >= 0 ? raw : rubberband(raw, height);
}

export type Sample = { y: number; t: number };

/** Pointer velocity in px/s over the last samples; 0 when the finger stopped before release. */
export function releaseVelocity(samples: Sample[]): number {
  const last = samples.at(-1);
  if (!last) return 0;
  const first = samples.find((s) => last.t - s.t <= VELOCITY_WINDOW_MS) ?? last;
  const dt = last.t - first.t;
  return dt > 0 ? ((last.y - first.y) / dt) * 1000 : 0;
}

/**
 * Close when the sheet, thrown at the release velocity, would come to rest past half its height.
 * The projection, not the release point, decides: a quick flick from near the top still closes.
 */
export function shouldDismiss(offset: number, velocity: number, height: number): boolean {
  return offset + project(velocity) > height / 2;
}
