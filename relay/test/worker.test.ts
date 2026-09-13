import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetCaches } from "../src/fcm";
import worker, { type Env } from "../src/worker";
import { ACTIVITY_TOKEN, DEVICE_TOKEN, fakeGoogle, generateServiceAccount } from "./helpers";

const ENV: Env = { FCM_SERVICE_ACCOUNT: "" };

let ipCounter = 0;
function nextIP(): string {
  ipCounter += 1;
  return `198.51.100.${ipCounter}`;
}

function request(body: unknown, options: { ip?: string; method?: string; path?: string } = {}) {
  return new Request(`https://relay.example${options.path ?? "/v1/push"}`, {
    method: options.method ?? "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": options.ip ?? nextIP() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const VALID_BODY = {
  deviceToken: DEVICE_TOKEN,
  liveActivityToken: ACTIVITY_TOKEN,
  aps: { event: "update", timestamp: 1700000000, "content-state": { phase: { type: "searching" }, sequence: 1 } },
};

describe("worker", () => {
  beforeEach(async () => {
    _resetCaches();
    ENV.FCM_SERVICE_ACCOUNT = JSON.stringify((await generateServiceAccount()).account);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("404s on any other path", async () => {
    const response = await worker.fetch(request(VALID_BODY, { path: "/other" }), ENV);
    expect(response.status).toBe(404);
  });

  it("405s on a non-POST request", async () => {
    const response = await worker.fetch(request(undefined, { method: "GET" }), ENV);
    expect(response.status).toBe(405);
  });

  it("rejects a body that isn't JSON", async () => {
    const response = await worker.fetch(new Request("https://relay.example/v1/push", {
      method: "POST", headers: { "cf-connecting-ip": nextIP() }, body: "{nope",
    }), ENV);
    expect(response.status).toBe(400);
  });

  it("rejects a malformed push before ever calling Google", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await worker.fetch(request({ ...VALID_BODY, aps: { event: "update" } }), ENV);
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a valid push to Firebase and proxies the result", async () => {
    const fetchMock = vi.fn(fakeGoogle(() => new Response('{"name":"projects/p/messages/1"}', { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    const response = await worker.fetch(request(VALID_BODY), ENV);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ name: "projects/p/messages/1" });
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit & { body: string }];
    expect(url).toBe("https://fcm.googleapis.com/v1/projects/overwatch-queue/messages:send");
    expect(JSON.parse(init.body).message.apns.payload.aps).toEqual(VALID_BODY.aps);
  });

  it("proxies a Firebase error verbatim", async () => {
    vi.stubGlobal("fetch", vi.fn(fakeGoogle(
      () => new Response('{"error":{"status":"UNREGISTERED"}}', { status: 404 })
    )));
    const response = await worker.fetch(request(VALID_BODY), ENV);
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: { status: "UNREGISTERED" } });
  });

  it("502s rather than crashing when Firebase can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    const response = await worker.fetch(request(VALID_BODY), ENV);
    expect(response.status).toBe(502);
  });

  it("rate-limits a single IP that sends too many requests too fast", async () => {
    vi.stubGlobal("fetch", vi.fn(fakeGoogle()));
    const ip = nextIP();
    let lastStatus = 0;
    for (let i = 0; i < 25; i++) {
      const response = await worker.fetch(request(VALID_BODY, { ip }), ENV);
      lastStatus = response.status;
    }
    expect(lastStatus).toBe(429);
  });
});
