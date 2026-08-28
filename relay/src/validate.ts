/**
 * What a PC is allowed to ask this relay to do — kept separate from `worker.ts` so it's
 * plain, dependency-free logic a unit test can hit directly.
 *
 * This is the relay's real defense, not the API key: the key only says "some copy of the
 * server app", not "this specific PC" — see `README.md`. What actually keeps the relay
 * from being a generic "send anything anywhere" proxy is that every field here is
 * strictly shaped, and a push can only ever reach the one device token it names.
 */
import type { PushRequest, PushType } from "./apns";

const TOKEN_PATTERN = /^[0-9a-fA-F]{32,200}$/;
const MAX_TEXT_LENGTH = 200;
const MAX_SESSION_ID_LENGTH = 64;

const PUSH_TYPES: readonly PushType[] =
  ["background", "alert", "liveActivityStart", "liveActivityUpdate", "liveActivityEnd"];
const LIVE_ACTIVITY_TYPES: readonly PushType[] =
  ["liveActivityStart", "liveActivityUpdate", "liveActivityEnd"];

function isNonNegativeInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `undefined` when absent (fine, only `alert` requires one), an error string when
 * present but malformed. */
function optionalTextError(b: Record<string, unknown>, key: "title" | "body"): string | null {
  if (b[key] === undefined) return null;
  if (typeof b[key] !== "string" || (b[key] as string).length > MAX_TEXT_LENGTH) {
    return `${key} must be a string of at most ${MAX_TEXT_LENGTH} characters`;
  }
  return null;
}

export function validatePushRequest(body: unknown): PushRequest | string {
  if (typeof body !== "object" || body === null) return "body must be a JSON object";
  const b = body as Record<string, unknown>;

  if (typeof b.token !== "string" || !TOKEN_PATTERN.test(b.token)) return "token must be a hex device token";
  if (b.environment !== "sandbox" && b.environment !== "production") {
    return "environment must be \"sandbox\" or \"production\"";
  }
  if (!PUSH_TYPES.includes(b.pushType as PushType)) {
    return `pushType must be one of ${PUSH_TYPES.join(", ")}`;
  }
  const pushType = b.pushType as PushType;

  const titleError = optionalTextError(b, "title");
  if (titleError) return titleError;
  const bodyError = optionalTextError(b, "body");
  if (bodyError) return bodyError;

  if (LIVE_ACTIVITY_TYPES.includes(pushType)) {
    return validateLiveActivityRequest(b, pushType);
  }

  // background / alert — reaches a phone or watch device token directly.
  if (b.kind !== "phone" && b.kind !== "watch") return "kind must be \"phone\" or \"watch\"";
  if (typeof b.sessionId !== "string" || b.sessionId.length === 0
      || b.sessionId.length > MAX_SESSION_ID_LENGTH) {
    return "sessionId must be a non-empty string";
  }
  if (!isNonNegativeInt(b.sequence)) return "sequence must be a non-negative integer";

  return {
    kind: b.kind, token: b.token, environment: b.environment, pushType,
    sessionId: b.sessionId, sequence: b.sequence,
    title: b.title as string | undefined, body: b.body as string | undefined,
  };
}

function validateLiveActivityRequest(b: Record<string, unknown>, pushType: PushType): PushRequest | string {
  if (!isNonNegativeInt(b.timestamp)) return "timestamp must be a non-negative integer";
  if (!isPlainObject(b.contentState)) return "contentState must be an object";

  const request: PushRequest = {
    token: b.token as string, environment: b.environment as PushRequest["environment"],
    pushType, timestamp: b.timestamp as number, contentState: b.contentState,
    title: b.title as string | undefined, body: b.body as string | undefined,
  };

  if (pushType === "liveActivityStart") {
    if (!isPlainObject(b.attributes)) return "attributes must be an object";
    const attributes = b.attributes;
    if (typeof attributes.sessionID !== "string" || attributes.sessionID.length === 0) {
      return "attributes.sessionID must be a non-empty string";
    }
    if (typeof attributes.startedAt !== "number") return "attributes.startedAt must be a number";
    request.attributes = { sessionID: attributes.sessionID, startedAt: attributes.startedAt };
  }

  if (pushType === "liveActivityUpdate" && b.staleDate !== undefined) {
    if (!isNonNegativeInt(b.staleDate)) return "staleDate must be a non-negative integer";
    request.staleDate = b.staleDate;
  }

  if (pushType === "liveActivityEnd" && b.dismissalDate !== undefined) {
    if (!isNonNegativeInt(b.dismissalDate)) return "dismissalDate must be a non-negative integer";
    request.dismissalDate = b.dismissalDate;
  }

  return request;
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
