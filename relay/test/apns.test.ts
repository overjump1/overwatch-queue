import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetCaches, sendPush, signedAuthToken, type ApnsConfig } from "../src/apns";

function pemFrom(der: ArrayBuffer): string {
  const bytes = new Uint8Array(der);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const base64 = btoa(binary).match(/.{1,64}/g)!.join("\n");
  return `-----BEGIN PRIVATE KEY-----\n${base64}\n-----END PRIVATE KEY-----\n`;
}

function base64urlDecode(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(text.length + ((4 - (text.length % 4)) % 4), "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

async function generateConfig(): Promise<{ config: ApnsConfig; publicKey: CryptoKey }> {
  const pair = await crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]
  ) as CryptoKeyPair;
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey) as ArrayBuffer;
  return {
    config: {
      teamId: "TEAM1234",
      keyId: "ABC123",
      bundleIdIOS: "com.tomerady.OverwatchQueue",
      bundleIdWatch: "com.tomerady.OverwatchQueue.watchkitapp",
      privateKeyPEM: pemFrom(pkcs8),
    },
    publicKey: pair.publicKey,
  };
}

describe("signedAuthToken", () => {
  beforeEach(() => _resetCaches());

  it("produces a JWT with a verifiable ES256 signature and the right claims", async () => {
    const { config, publicKey } = await generateConfig();
    const jwt = await signedAuthToken(config);
    const [headerB64, payloadB64, signatureB64] = jwt.split(".");

    const header = JSON.parse(new TextDecoder().decode(base64urlDecode(headerB64)));
    expect(header).toEqual({ alg: "ES256", kid: "ABC123" });

    const payload = JSON.parse(new TextDecoder().decode(base64urlDecode(payloadB64)));
    expect(payload.iss).toBe("TEAM1234");
    expect(typeof payload.iat).toBe("number");

    const valid = await crypto.subtle.verify(
      { name: "ECDSA", hash: "SHA-256" },
      publicKey,
      base64urlDecode(signatureB64),
      new TextEncoder().encode(`${headerB64}.${payloadB64}`)
    );
    expect(valid).toBe(true);
  });

  it("reuses the cached token within the lifetime window", async () => {
    const { config } = await generateConfig();
    const first = await signedAuthToken(config, 1_000_000);
    const second = await signedAuthToken(config, 1_000_000 + 60_000);
    expect(second).toBe(first);
  });

  it("signs a new token once the lifetime window has passed", async () => {
    const { config } = await generateConfig();
    const first = await signedAuthToken(config, 1_000_000);
    const second = await signedAuthToken(config, 1_000_000 + 51 * 60 * 1000);
    expect(second).not.toBe(first);
  });
});

describe("sendPush", () => {
  beforeEach(() => _resetCaches());
  afterEach(() => vi.unstubAllGlobals());

  it("posts a background push to the sandbox host with the phone's bundle id", async () => {
    const { config } = await generateConfig();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await sendPush(config, {
      kind: "phone", token: "abc123", environment: "sandbox", pushType: "background",
      sessionId: "s1", sequence: 3,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.sandbox.push.apple.com/3/device/abc123");
    expect(init.headers["apns-topic"]).toBe("com.tomerady.OverwatchQueue");
    expect(init.headers["apns-push-type"]).toBe("background");
    expect(init.headers["apns-priority"]).toBe("5");
    expect(JSON.parse(init.body)).toEqual({
      aps: { "content-available": 1 }, sessionID: "s1", sequence: 3,
    });
  });

  it("posts an alert push to the production host with the watch's bundle id", async () => {
    const { config } = await generateConfig();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await sendPush(config, {
      kind: "watch", token: "def456", environment: "production", pushType: "alert",
      sessionId: "s2", sequence: 9, title: "Match Found", body: "Get back to your PC.",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.push.apple.com/3/device/def456");
    expect(init.headers["apns-topic"]).toBe("com.tomerady.OverwatchQueue.watchkitapp");
    expect(init.headers["apns-priority"]).toBe("10");
    expect(JSON.parse(init.body)).toEqual({
      aps: { alert: { title: "Match Found", body: "Get back to your PC." }, sound: "default",
            "interruption-level": "time-sensitive" },
      sessionID: "s2", sequence: 9,
    });
  });

  it("proxies Apple's status and body back verbatim", async () => {
    const { config } = await generateConfig();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response('{"reason":"BadDeviceToken"}', { status: 400 })
    ));

    const result = await sendPush(config, {
      kind: "phone", token: "abc123", environment: "sandbox", pushType: "background",
      sessionId: "s1", sequence: 1,
    });

    expect(result.status).toBe(400);
    expect(JSON.parse(result.body)).toEqual({ reason: "BadDeviceToken" });
  });

  it("posts a Live Activity start to the .start topic with attributes and no kind needed", async () => {
    const { config } = await generateConfig();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await sendPush(config, {
      token: "start-token", environment: "sandbox", pushType: "liveActivityStart",
      timestamp: 1700000000,
      attributes: { sessionID: "abc", startedAt: 721692800.0 },
      contentState: { phase: { type: "idle" }, sequence: 0 },
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.sandbox.push.apple.com/3/device/start-token");
    // Not `.push-type.liveactivity.start` — Apple rejects that suffix for a real
    // push-to-start token (`TopicDisallowed`); see the comment on `topicFor`.
    expect(init.headers["apns-topic"]).toBe("com.tomerady.OverwatchQueue.push-type.liveactivity");
    expect(init.headers["apns-push-type"]).toBe("liveactivity");
    expect(init.headers["apns-priority"]).toBe("10");
    const body = JSON.parse(init.body);
    expect(body.aps.event).toBe("start");
    expect(body.aps["attributes-type"]).toBe("QueueActivityAttributes");
    expect(body.aps.attributes).toEqual({ sessionID: "abc", startedAt: 721692800.0 });
    expect(body.aps.alert).toBeUndefined();
  });

  it("includes an alert on a start push when title/body are given", async () => {
    const { config } = await generateConfig();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await sendPush(config, {
      token: "start-token", environment: "sandbox", pushType: "liveActivityStart",
      timestamp: 1, attributes: { sessionID: "abc", startedAt: 0 }, contentState: {},
      title: "Match Found", body: "Get back to your PC.",
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.aps.alert).toEqual({ title: "Match Found", body: "Get back to your PC." });
  });

  it("posts a Live Activity update to the plain topic, no .start suffix", async () => {
    const { config } = await generateConfig();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await sendPush(config, {
      token: "activity-token", environment: "production", pushType: "liveActivityUpdate",
      timestamp: 1700000005, contentState: { phase: { type: "searching" }, sequence: 3 },
      staleDate: 1700000600,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.push.apple.com/3/device/activity-token");
    expect(init.headers["apns-topic"]).toBe("com.tomerady.OverwatchQueue.push-type.liveactivity");
    expect(init.headers["apns-push-type"]).toBe("liveactivity");
    expect(init.headers["apns-priority"]).toBe("10");
    const body = JSON.parse(init.body);
    expect(body.aps.event).toBe("update");
    expect(body.aps["stale-date"]).toBe(1700000600);
    expect(body.aps["content-state"]).toEqual({ phase: { type: "searching" }, sequence: 3 });
  });

  it("posts a Live Activity end with the end event and an optional dismissalDate", async () => {
    const { config } = await generateConfig();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await sendPush(config, {
      token: "activity-token", environment: "production", pushType: "liveActivityEnd",
      timestamp: 1700000009, contentState: { phase: { type: "idle" } }, dismissalDate: 1700000700,
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.aps.event).toBe("end");
    expect(body.aps["dismissal-date"]).toBe(1700000700);
    expect(fetchMock.mock.calls[0][1].headers["apns-topic"])
      .toBe("com.tomerady.OverwatchQueue.push-type.liveactivity");
  });
});
