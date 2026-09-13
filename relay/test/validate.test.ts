import { describe, expect, it } from "vitest";
import { validatePushRequest } from "../src/validate";
import { ACTIVITY_TOKEN, DEVICE_TOKEN } from "./helpers";

const STATE = { phase: { type: "searching" }, sequence: 3 };

function start(aps: Record<string, unknown> = {}) {
  return {
    deviceToken: DEVICE_TOKEN, liveActivityToken: ACTIVITY_TOKEN,
    aps: {
      event: "start", timestamp: 1700, "content-state": STATE,
      "attributes-type": "QueueActivityAttributes",
      attributes: { sessionID: "s-1", startedAt: 721692800.5 },
      alert: { title: "Queue started", body: "Tank" },
      ...aps,
    },
  };
}

function update(aps: Record<string, unknown> = {}) {
  return {
    deviceToken: DEVICE_TOKEN, liveActivityToken: ACTIVITY_TOKEN,
    aps: { event: "update", timestamp: 1700, "content-state": STATE, ...aps },
  };
}

describe("validatePushRequest", () => {
  it("accepts each of the three events the server sends", () => {
    expect(validatePushRequest(start())).toEqual(start());
    expect(validatePushRequest(update({ "stale-date": 1900 }))).toEqual(update({ "stale-date": 1900 }));
    const end = { ...update(), aps: { event: "end", timestamp: 1700, "content-state": STATE, "dismissal-date": 1760 } };
    expect(validatePushRequest(end)).toEqual(end);
  });

  it("rejects anything that isn't an object", () => {
    expect(typeof validatePushRequest(null)).toBe("string");
    expect(typeof validatePushRequest([update()])).toBe("string");
  });

  it("requires both tokens, well-formed", () => {
    expect(validatePushRequest({ ...update(), deviceToken: undefined })).toMatch(/deviceToken/);
    expect(validatePushRequest({ ...update(), deviceToken: "has spaces in it, and more" })).toMatch(/deviceToken/);
    expect(validatePushRequest({ ...update(), liveActivityToken: "not-hex-at-all-not-hex-at-all-xx" })).toMatch(/liveActivityToken/);
  });

  it("only sends Live Activity events", () => {
    expect(validatePushRequest(update({ event: "background" }))).toMatch(/aps.event/);
  });

  it("refuses keys that would turn it into some other kind of push", () => {
    expect(validatePushRequest(update({ "content-available": 1 }))).toMatch(/content-available/);
    expect(validatePushRequest(update({ "attributes-type": "QueueActivityAttributes" }))).toMatch(/not allowed on update/);
    expect(validatePushRequest(start({ "stale-date": 1 }))).toMatch(/not allowed on start/);
  });

  it("requires a timestamp and content-state", () => {
    expect(validatePushRequest(update({ timestamp: -1 }))).toMatch(/timestamp/);
    expect(validatePushRequest(update({ "content-state": "searching" }))).toMatch(/content-state/);
  });

  it("only starts this app's own activity", () => {
    expect(validatePushRequest(start({ "attributes-type": "SomeOtherAttributes" }))).toMatch(/attributes-type/);
    expect(validatePushRequest(start({ attributes: { sessionID: "", startedAt: 1 } }))).toMatch(/sessionID/);
    expect(validatePushRequest(start({ attributes: { sessionID: "s", startedAt: "now" } }))).toMatch(/startedAt/);
    expect(validatePushRequest(start({ attributes: { sessionID: "s", startedAt: 1, extra: true } }))).toMatch(/only carry/);
  });

  it("keeps an alert short and plain", () => {
    expect(validatePushRequest(update({ alert: "hi" }))).toMatch(/alert must be an object/);
    expect(validatePushRequest(update({ alert: { title: "x".repeat(201) } }))).toMatch(/alert.title/);
    expect(validatePushRequest(update({ alert: { "launch-image": "x" } }))).toMatch(/launch-image/);
    expect(validatePushRequest(update({ alert: { title: "Match found", body: "Tank", sound: "default" } })))
      .not.toBeTypeOf("string");
  });

  it("refuses a payload bigger than Apple would deliver", () => {
    expect(validatePushRequest(update({ "content-state": { blob: "x".repeat(5000) } }))).toMatch(/bytes/);
  });
});
