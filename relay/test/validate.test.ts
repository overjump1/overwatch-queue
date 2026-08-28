import { describe, expect, it } from "vitest";
import { constantTimeEqual, validatePushRequest } from "../src/validate";

const VALID = {
  kind: "phone",
  token: "a".repeat(64),
  environment: "sandbox",
  pushType: "background",
  sessionId: "3F2504E0-4F89-41D3-9A0C-0305E82C3301",
  sequence: 7,
};

describe("validatePushRequest", () => {
  it("accepts a well-formed background push", () => {
    const result = validatePushRequest(VALID);
    expect(typeof result).not.toBe("string");
  });

  it("accepts a well-formed alert push with title and body", () => {
    const result = validatePushRequest({ ...VALID, pushType: "alert", title: "Match Found", body: "Get back to your PC." });
    expect(typeof result).not.toBe("string");
  });

  it("rejects a non-object body", () => {
    expect(validatePushRequest("nope")).toBe("body must be a JSON object");
    expect(validatePushRequest(null)).toBe("body must be a JSON object");
  });

  it("rejects an unknown kind", () => {
    expect(validatePushRequest({ ...VALID, kind: "tablet" })).toMatch(/kind/);
  });

  it("rejects a token that isn't hex", () => {
    expect(validatePushRequest({ ...VALID, token: "not-hex!" })).toMatch(/token/);
  });

  it("rejects a token that's too short", () => {
    expect(validatePushRequest({ ...VALID, token: "abc" })).toMatch(/token/);
  });

  it("rejects an unknown environment", () => {
    expect(validatePushRequest({ ...VALID, environment: "staging" })).toMatch(/environment/);
  });

  it("rejects an unknown pushType", () => {
    expect(validatePushRequest({ ...VALID, pushType: "silent" })).toMatch(/pushType/);
  });

  it("rejects a missing sessionId", () => {
    const { sessionId, ...rest } = VALID;
    expect(validatePushRequest(rest)).toMatch(/sessionId/);
  });

  it("rejects a non-integer sequence", () => {
    expect(validatePushRequest({ ...VALID, sequence: 1.5 })).toMatch(/sequence/);
    expect(validatePushRequest({ ...VALID, sequence: -1 })).toMatch(/sequence/);
    expect(validatePushRequest({ ...VALID, sequence: "7" })).toMatch(/sequence/);
  });

  it("rejects an oversized title", () => {
    expect(validatePushRequest({ ...VALID, pushType: "alert", title: "x".repeat(500) })).toMatch(/title/);
  });
});

describe("constantTimeEqual", () => {
  it("matches identical strings", () => {
    expect(constantTimeEqual("abc123", "abc123")).toBe(true);
  });

  it("rejects differing strings of the same length", () => {
    expect(constantTimeEqual("abc123", "abc124")).toBe(false);
  });

  it("rejects strings of differing length", () => {
    expect(constantTimeEqual("abc", "abcd")).toBe(false);
  });
});
