import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  _resetCaches, accessToken, messageFor, parseServiceAccount, sendPush, signedAssertion,
} from "../src/fcm";
import { ACTIVITY_TOKEN, DEVICE_TOKEN, base64urlDecode, fakeGoogle, generateServiceAccount } from "./helpers";

const APS = { event: "update", timestamp: 1700, "content-state": { phase: "searching" } };

describe("parseServiceAccount", () => {
  it("reads the fields it needs", () => {
    const account = parseServiceAccount(JSON.stringify({
      type: "service_account", project_id: "p", client_email: "e", private_key: "k",
    }));
    expect(account.project_id).toBe("p");
  });

  it("refuses one missing its key", () => {
    expect(() => parseServiceAccount(JSON.stringify({ project_id: "p", client_email: "e" }))).toThrow();
  });
});

describe("signedAssertion", () => {
  beforeEach(() => _resetCaches());

  it("is an RS256 JWT Google can verify, scoped to Firebase messaging", async () => {
    const { account, publicKey } = await generateServiceAccount();
    const jwt = await signedAssertion(account, 1_700_000_000_000);
    const [headerB64, payloadB64, signatureB64] = jwt.split(".");

    expect(JSON.parse(new TextDecoder().decode(base64urlDecode(headerB64)))).toEqual({ alg: "RS256", typ: "JWT" });
    const payload = JSON.parse(new TextDecoder().decode(base64urlDecode(payloadB64)));
    expect(payload).toEqual({
      iss: account.client_email,
      scope: "https://www.googleapis.com/auth/firebase.messaging",
      aud: "https://oauth2.googleapis.com/token",
      iat: 1_700_000_000,
      exp: 1_700_003_600,
    });

    const valid = await crypto.subtle.verify(
      "RSASSA-PKCS1-v1_5", publicKey, base64urlDecode(signatureB64),
      new TextEncoder().encode(`${headerB64}.${payloadB64}`)
    );
    expect(valid).toBe(true);
  });
});

describe("accessToken", () => {
  beforeEach(() => _resetCaches());
  afterEach(() => vi.unstubAllGlobals());

  it("exchanges the assertion and reuses the token until near expiry", async () => {
    const { account } = await generateServiceAccount();
    const fetchMock = vi.fn(fakeGoogle());
    vi.stubGlobal("fetch", fetchMock);

    expect(await accessToken(account, 1_000_000)).toBe("ya29.fake");
    await accessToken(account, 1_000_000 + 50 * 60 * 1000);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const body = new URLSearchParams(fetchMock.mock.calls[0][1]!.body);
    expect(body.get("grant_type")).toBe("urn:ietf:params:oauth:grant-type:jwt-bearer");
    expect(body.get("assertion")?.split(".")).toHaveLength(3);
  });

  it("fetches a new one once the old is about to expire", async () => {
    const { account } = await generateServiceAccount();
    const fetchMock = vi.fn(fakeGoogle());
    vi.stubGlobal("fetch", fetchMock);

    await accessToken(account, 1_000_000);
    await accessToken(account, 1_000_000 + 56 * 60 * 1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("throws when Google refuses the service account", async () => {
    const { account } = await generateServiceAccount();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("invalid_grant", { status: 400 })));
    await expect(accessToken(account)).rejects.toThrow(/400/);
  });
});

describe("messageFor", () => {
  it("addresses the device and the card separately, as a Live Activity", () => {
    const message = messageFor({ deviceToken: DEVICE_TOKEN, liveActivityToken: ACTIVITY_TOKEN, aps: APS });
    expect(message).toEqual({
      message: {
        token: DEVICE_TOKEN,
        apns: {
          live_activity_token: ACTIVITY_TOKEN,
          headers: { "apns-priority": "10", "apns-push-type": "liveactivity" },
          payload: { aps: APS },
        },
      },
    });
  });
});

describe("sendPush", () => {
  beforeEach(() => _resetCaches());
  afterEach(() => vi.unstubAllGlobals());

  it("posts to the project's send endpoint with the access token", async () => {
    const { account } = await generateServiceAccount();
    const fetchMock = vi.fn(fakeGoogle());
    vi.stubGlobal("fetch", fetchMock);

    const result = await sendPush(account, { deviceToken: DEVICE_TOKEN, liveActivityToken: ACTIVITY_TOKEN, aps: APS });

    expect(result.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit & { headers: Record<string, string>; body: string }];
    expect(url).toBe("https://fcm.googleapis.com/v1/projects/overwatch-queue/messages:send");
    expect(init.headers.authorization).toBe("Bearer ya29.fake");
    expect(JSON.parse(init.body).message.token).toBe(DEVICE_TOKEN);
  });

  it("hands back Firebase's refusal untouched", async () => {
    const { account } = await generateServiceAccount();
    const refusal = '{"error":{"status":"UNREGISTERED"}}';
    vi.stubGlobal("fetch", vi.fn(fakeGoogle(() => new Response(refusal, { status: 404 }))));

    const result = await sendPush(account, { deviceToken: DEVICE_TOKEN, liveActivityToken: ACTIVITY_TOKEN, aps: APS });
    expect(result).toEqual({ status: 404, body: refusal });
  });
});
