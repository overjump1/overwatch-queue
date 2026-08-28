/**
 * What a PC is allowed to ask this relay to do — kept separate from `worker.ts` so it's
 * plain, dependency-free logic a unit test can hit directly.
 *
 * This is the relay's real defense, not the API key: the key only says "some copy of the
 * server app", not "this specific PC" — see `README.md`. What actually keeps the relay
 * from being a generic "send anything anywhere" proxy is that every field here is
 * strictly shaped, and a push can only ever reach the one device token it names.
 */
import type { PushRequest } from "./apns";

const TOKEN_PATTERN = /^[0-9a-fA-F]{32,200}$/;
const MAX_TEXT_LENGTH = 200;

export function validatePushRequest(body: unknown): PushRequest | string {
  if (typeof body !== "object" || body === null) return "body must be a JSON object";
  const b = body as Record<string, unknown>;

  if (b.kind !== "phone" && b.kind !== "watch") return "kind must be \"phone\" or \"watch\"";
  if (typeof b.token !== "string" || !TOKEN_PATTERN.test(b.token)) return "token must be a hex device token";
  if (b.environment !== "sandbox" && b.environment !== "production") {
    return "environment must be \"sandbox\" or \"production\"";
  }
  if (b.pushType !== "background" && b.pushType !== "alert") {
    return "pushType must be \"background\" or \"alert\"";
  }
  if (typeof b.sessionId !== "string" || b.sessionId.length === 0 || b.sessionId.length > 64) {
    return "sessionId must be a non-empty string";
  }
  if (typeof b.sequence !== "number" || !Number.isInteger(b.sequence) || b.sequence < 0) {
    return "sequence must be a non-negative integer";
  }
  if (b.title !== undefined && (typeof b.title !== "string" || b.title.length > MAX_TEXT_LENGTH)) {
    return `title must be a string of at most ${MAX_TEXT_LENGTH} characters`;
  }
  if (b.body !== undefined && (typeof b.body !== "string" || b.body.length > MAX_TEXT_LENGTH)) {
    return `body must be a string of at most ${MAX_TEXT_LENGTH} characters`;
  }

  return {
    kind: b.kind,
    token: b.token,
    environment: b.environment,
    pushType: b.pushType,
    sessionId: b.sessionId,
    sequence: b.sequence,
    title: b.title as string | undefined,
    body: b.body as string | undefined,
  };
}

/** Constant-time comparison for the API key — the same reasoning as
 * `pairing.py`'s `_constant_time_equal`: a naive `===` leaks timing information about
 * how many leading characters matched. */
export function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i++) {
    difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return difference === 0;
}
