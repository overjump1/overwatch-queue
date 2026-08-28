import { describe, expect, it } from "vitest";
import { isRateLimited } from "../src/rateLimit";

describe("isRateLimited", () => {
  it("allows a burst under the limit", () => {
    const ip = "203.0.113.1";
    const now = Date.now();
    for (let i = 0; i < 20; i++) {
      expect(isRateLimited(ip, now)).toBe(false);
    }
  });

  it("blocks once the window's limit is exceeded", () => {
    const ip = "203.0.113.2";
    const now = Date.now();
    for (let i = 0; i < 20; i++) isRateLimited(ip, now);
    expect(isRateLimited(ip, now)).toBe(true);
  });

  it("resets once the window has passed", () => {
    const ip = "203.0.113.3";
    const now = Date.now();
    for (let i = 0; i < 20; i++) isRateLimited(ip, now);
    expect(isRateLimited(ip, now + 11_000)).toBe(false);
  });

  it("tracks IPs independently", () => {
    const now = Date.now();
    for (let i = 0; i < 20; i++) isRateLimited("203.0.113.4", now);
    expect(isRateLimited("203.0.113.5", now)).toBe(false);
  });
});
