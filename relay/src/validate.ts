/**
 * What a PC is allowed to ask this relay to do — kept separate from `worker.ts` so it's
 * plain, dependency-free logic a unit test can hit directly.
 *
 * This is the relay's real defense. There is no API key: `server/` is open source, so
 * any key it carried would be public, and pretending otherwise would only hide where the
 * protection actually is. What keeps the relay from being a generic "send anything to
 * anyone" proxy is:
 *
 * - it only ever sends a Live Activity push for this app's `QueueActivityAttributes` —
 *   the push type and priority are fixed in `fcm.ts`, and every `aps` key is checked here;
 * - a push can only reach the device *and* card whose two tokens it names, and those
 *   tokens are only ever handed to the one PC the phone paired with over its own LAN;
 * - `rateLimit.ts` throttles each caller.
 */
import type { LiveActivityEvent, PushRequest } from "./fcm";

// FCM registration tokens are URL-safe base64 with a `:`; ActivityKit tokens are hex.
const DEVICE_TOKEN_PATTERN = /^[A-Za-z0-9_:-]{20,512}$/;
const ACTIVITY_TOKEN_PATTERN = /^[0-9a-fA-F]{32,400}$/;
const MAX_TEXT_LENGTH = 200;
const MAX_SESSION_ID_LENGTH = 64;
// Apple's own ceiling for a Live Activity payload.
const MAX_APS_BYTES = 4096;

const EVENTS: readonly LiveActivityEvent[] = ["start", "update", "end"];
const ALLOWED_APS_KEYS: Record<LiveActivityEvent, readonly string[]> = {
  start: ["event", "timestamp", "content-state", "attributes-type", "attributes", "alert", "interruption-level"],
  update: ["event", "timestamp", "content-state", "stale-date", "alert", "interruption-level"],
  end: ["event", "timestamp", "content-state", "dismissal-date", "alert", "interruption-level"],
};
// Not `critical`: that needs an entitlement this app doesn't have, and would bypass the
// ringer switch. Not `passive` either — there'd be no reason to send an alert at all.
const INTERRUPTION_LEVELS = ["active", "time-sensitive"];

function isNonNegativeInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isShortText(value: unknown): value is string {
  return typeof value === "string" && value.length <= MAX_TEXT_LENGTH;
}

function alertError(alert: unknown): string | null {
  if (!isPlainObject(alert)) return "aps.alert must be an object";
  for (const key of Object.keys(alert)) {
    if (!["title", "body", "sound"].includes(key)) return `aps.alert.${key} is not allowed`;
    if (!isShortText(alert[key])) return `aps.alert.${key} must be a string of at most ${MAX_TEXT_LENGTH} characters`;
  }
  return null;
}

export function validatePushRequest(body: unknown): PushRequest | string {
  if (!isPlainObject(body)) return "body must be a JSON object";

  if (typeof body.deviceToken !== "string" || !DEVICE_TOKEN_PATTERN.test(body.deviceToken)) {
    return "deviceToken must be an FCM registration token";
  }
  if (typeof body.liveActivityToken !== "string" || !ACTIVITY_TOKEN_PATTERN.test(body.liveActivityToken)) {
    return "liveActivityToken must be a hex ActivityKit token";
  }
  if (!isPlainObject(body.aps)) return "aps must be an object";
  const aps = body.aps;

  if (!EVENTS.includes(aps.event as LiveActivityEvent)) return `aps.event must be one of ${EVENTS.join(", ")}`;
  const event = aps.event as LiveActivityEvent;

  for (const key of Object.keys(aps)) {
    if (!ALLOWED_APS_KEYS[event].includes(key)) return `aps.${key} is not allowed on ${event}`;
  }
  if (!isNonNegativeInt(aps.timestamp)) return "aps.timestamp must be a non-negative integer";
  if (!isPlainObject(aps["content-state"])) return "aps.content-state must be an object";

  if (event === "start") {
    if (aps["attributes-type"] !== "QueueActivityAttributes") {
      return "aps.attributes-type must be QueueActivityAttributes";
    }
    const attributes = aps.attributes;
    if (!isPlainObject(attributes)) return "aps.attributes must be an object";
    if (Object.keys(attributes).some((key) => key !== "sessionID" && key !== "startedAt")) {
      return "aps.attributes may only carry sessionID and startedAt";
    }
    if (typeof attributes.sessionID !== "string" || attributes.sessionID.length === 0
        || attributes.sessionID.length > MAX_SESSION_ID_LENGTH) {
      return "aps.attributes.sessionID must be a non-empty string";
    }
    if (typeof attributes.startedAt !== "number" || !Number.isFinite(attributes.startedAt)) {
      return "aps.attributes.startedAt must be a number";
    }
  }
  if (aps["stale-date"] !== undefined && !isNonNegativeInt(aps["stale-date"])) {
    return "aps.stale-date must be a non-negative integer";
  }
  if (aps["dismissal-date"] !== undefined && !isNonNegativeInt(aps["dismissal-date"])) {
    return "aps.dismissal-date must be a non-negative integer";
  }
  if (aps.alert !== undefined) {
    const error = alertError(aps.alert);
    if (error) return error;
  }
  if (aps["interruption-level"] !== undefined) {
    if (aps.alert === undefined) return "aps.interruption-level needs an aps.alert";
    if (!INTERRUPTION_LEVELS.includes(aps["interruption-level"] as string)) {
      return `aps.interruption-level must be one of ${INTERRUPTION_LEVELS.join(", ")}`;
    }
  }

  if (new TextEncoder().encode(JSON.stringify(aps)).length > MAX_APS_BYTES) {
    return `aps must be at most ${MAX_APS_BYTES} bytes`;
  }

  return { deviceToken: body.deviceToken, liveActivityToken: body.liveActivityToken, aps };
}
