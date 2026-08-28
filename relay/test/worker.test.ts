import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetCaches } from "../src/apns";
import worker, { type Env } from "../src/worker";

const ENV: Env = {
  APNS_TEAM_ID: "TEAM1234",
  APNS_KEY_ID: "ABC123",
  APNS_BUNDLE_ID_IOS: "com.tomerady.OverwatchQueue",
  APNS_BUNDLE_ID_WATCH: "com.tomerady.OverwatchQueue.watchkitapp",
  // A throwaway PKCS8 P-256 key generated for this test file only.
  APNS_PRIVATE_KEY: "",
  RELAY_API_KEY: "test-relay-key",
};

let ipCounter = 0;
function nextIP(): string {
  ipCounter += 1;
  return `198.51.100.${ipCounter}`;
}

function request(body: unknown, options: { auth?: string; ip?: string; method?: string; path?: string } = {}) {
  return new Request(`https://relay.example${options.path ?? "/v1/push"}`, {
    method: options.method ?? "POST",
    headers: {
      "content-type": "application/json",
      "authorization": options.auth ?? `Bearer ${ENV.RELAY_API_KEY}`,
      "cf-connecting-ip": options.ip ?? nextIP(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const VALID_BODY = {
  kind: "phone",
  token: "a".repeat(64),
  environment: "sandbox",
  pushType: "background",
  sessionId: "s1",
  sequence: 1,
};

describe("worker", () => {
  beforeEach(async () => {
    _resetCaches();
    const pair = await crypto.subtle.generateKey(
      { name: "ECDSA", namedCurve: "P-256" }, true, ["sign"]
    ) as CryptoKeyPair;
    const pkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey) as ArrayBuffer;
    const bytes = new Uint8Array(pkcs8);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    ENV.APNS_PRIVATE_KEY = `-----BEGIN PRIVATE KEY-----\n${btoa(binary)}\n-----END PRIVATE KEY-----\n`;
  });

  afterEach(() => vi.unstubAllGlobals());

  it("rejects a request with no Authorization header", async () => {
    const response = await worker.fetch(request(VALID_BODY, { auth: "" }), ENV);
    expect(response.status).toBe(401);
  });

  it("rejects a request with the wrong key", async () => {
    const response = await worker.fetch(request(VALID_BODY, { auth: "Bearer wrong" }), ENV);
    expect(response.status).toBe(401);
  });

  it("rejects a malformed body", async () => {
    const response = await worker.fetch(request({ ...VALID_BODY, kind: "tablet" }), ENV);
    expect(response.status).toBe(400);
  });

  it("404s on any other path", async () => {
    const response = await worker.fetch(request(VALID_BODY, { path: "/other" }), ENV);
    expect(response.status).toBe(404);
  });

  it("405s on a non-POST request", async () => {
    const response = await worker.fetch(request(undefined, { method: "GET" }), ENV);
    expect(response.status).toBe(405);
  });

  it("forwards a valid request to Apple and proxies the result", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    const response = await worker.fetch(request(VALID_BODY), ENV);
    expect(response.status).toBe(200);
  });

  it("proxies an Apple error verbatim", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response('{"reason":"Unregistered"}', { status: 410 })
    ));
    const response = await worker.fetch(request(VALID_BODY), ENV);
    expect(response.status).toBe(410);
    expect(await response.json()).toEqual({ reason: "Unregistered" });
  });

  it("rate-limits a single IP that sends too many requests too fast", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    const ip = nextIP();
    let lastStatus = 0;
    for (let i = 0; i < 25; i++) {
      const response = await worker.fetch(request(VALID_BODY, { ip }), ENV);
      lastStatus = response.status;
    }
    expect(lastStatus).toBe(429);
  });
});
