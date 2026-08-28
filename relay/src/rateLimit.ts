/**
 * A best-effort per-IP limiter. Deliberately simple: an in-memory sliding window keyed
 * by `CF-Connecting-IP`.
 *
 * Honest limitation: this state lives in one Worker isolate, and Cloudflare may run
 * several isolates for the same Worker at once, or replace one after any idle period —
 * there's no guarantee two requests from the same abusive IP land on the same isolate.
 * At this project's actual scale (one relay, a handful of households' PCs) that's an
 * acceptable trade for zero extra infrastructure. If this relay ever sees real abuse,
 * replace this with a Durable Object (one global counter) or Cloudflare's native Rate
 * Limiting rules — both are a strict upgrade over this file, not a rewrite of the caller.
 */

const WINDOW_MS = 10_000;
const MAX_PER_WINDOW = 20;

const hits = new Map<string, number[]>();

export function isRateLimited(clientIP: string, now: number = Date.now()): boolean {
  const recent = (hits.get(clientIP) ?? []).filter((t) => now - t < WINDOW_MS);
  recent.push(now);
  hits.set(clientIP, recent);

  // Bound memory: an isolate that runs for a long time under varied IPs shouldn't grow
  // this map forever. Cheap to do here since it only runs when a key is touched anyway.
  if (hits.size > 10_000) {
    for (const [ip, times] of hits) {
      if (times.every((t) => now - t >= WINDOW_MS)) hits.delete(ip);
    }
  }

  return recent.length > MAX_PER_WINDOW;
}
