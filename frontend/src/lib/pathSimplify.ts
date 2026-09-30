/**
 * Ramer–Douglas–Peucker simplification of a freehand stroke (W4 ink).
 *
 * A pointer reports a point every few milliseconds; a signature-length
 * scribble is hundreds of them, most on a straight-enough line between their
 * neighbours. The server caps an ink element at 4000 points and a request at
 * 20 000 (contract v5), so the stroke is thinned BEFORE it is stored: every
 * point that lies within `tolerance` of the chord between the points kept
 * around it is dropped. At 0.5 pt the result is indistinguishable from the
 * raw stroke on paper (half a point is 0.18 mm, and the ink itself is 2 pt
 * wide by default) and typically 5–20× shorter.
 *
 * Iterative (explicit stack), so a very long stroke cannot overflow the call
 * stack. The first and last points are always kept.
 */

export interface XY {
  x: number;
  y: number;
}

/** The default tolerance, in PDF points (plan §5 W4). */
export const RDP_TOLERANCE_PT = 0.5;

/** Distance from `p` to the segment `a`–`b`. */
function distanceToSegment(p: XY, a: XY, b: XY): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

export function simplifyPath<T extends XY>(points: readonly T[], tolerance = RDP_TOLERANCE_PT): T[] {
  if (points.length <= 2) return [...points];
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack: Array<[number, number]> = [[0, points.length - 1]];
  while (stack.length > 0) {
    const [first, last] = stack.pop()!;
    let worst = -1;
    let worstIndex = -1;
    for (let index = first + 1; index < last; index += 1) {
      const distance = distanceToSegment(points[index], points[first], points[last]);
      if (distance > worst) {
        worst = distance;
        worstIndex = index;
      }
    }
    if (worstIndex >= 0 && worst > tolerance) {
      keep[worstIndex] = 1;
      stack.push([first, worstIndex], [worstIndex, last]);
    }
  }
  const out: T[] = [];
  points.forEach((point, index) => {
    if (keep[index]) out.push(point);
  });
  return out;
}

/** Consecutive duplicates dropped (a pointer that did not move reports the same point again). */
export function dedupe<T extends XY>(points: readonly T[]): T[] {
  const out: T[] = [];
  points.forEach((point) => {
    const last = out[out.length - 1];
    if (!last || last.x !== point.x || last.y !== point.y) out.push(point);
  });
  return out;
}
