/**
 * Everything about talking to Apple: signing the auth JWT and sending one push.
 *
 * Pulled out of `worker.ts` so it has no dependency on the Workers `Request`/`Response`
 * types — it can be unit-tested with plain values (see `test/apns.test.ts`).
 */

export interface ApnsConfig {
  teamId: string;
  keyId: string;
  bundleIdIOS: string;
  bundleIdWatch: string;
  privateKeyPEM: string;
}

export type Environment = "sandbox" | "production";
export type Kind = "phone" | "watch";
export type PushType = "background" | "alert" | "liveActivityStart" | "liveActivityUpdate" | "liveActivityEnd";

/**
 * One shape covering all five push types, with the fields each doesn't use left
 * `undefined` — simpler than a discriminated union for a request that's just come off
 * the wire as loosely-typed JSON anyway; `validate.ts` is what actually enforces which
 * fields a given `pushType` requires.
 */
export interface PushRequest {
  token: string;
  environment: Environment;
  pushType: PushType;
  // background / alert
  kind?: Kind;
  sessionId?: string;
  sequence?: number;
  title?: string;
  body?: string;
  // liveActivityStart / liveActivityUpdate / liveActivityEnd
  timestamp?: number;
  contentState?: unknown;
  attributes?: { sessionID: string; startedAt: number };
  staleDate?: number;
  dismissalDate?: number;
}

const LIVE_ACTIVITY_TYPES: readonly PushType[] =
  ["liveActivityStart", "liveActivityUpdate", "liveActivityEnd"];

const HOSTS: Record<Environment, string> = {
  production: "https://api.push.apple.com",
  sandbox: "https://api.sandbox.push.apple.com",
};

// Apple accepts a token signed up to an hour ago; refresh well inside that.
const TOKEN_LIFETIME_MS = 50 * 60 * 1000;

let cachedToken: { jwt: string; madeAt: number; keyId: string } | null = null;
let cachedKey: { pem: string; key: CryptoKey } | null = null;

function base64url(bytes: ArrayBuffer | Uint8Array): string {
  const array = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (const byte of array) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function importPrivateKey(pem: string): Promise<CryptoKey> {
  if (cachedKey && cachedKey.pem === pem) return cachedKey.key;
  const body = pem
    .replace(/-----BEGIN PRIVATE KEY-----/, "")
    .replace(/-----END PRIVATE KEY-----/, "")
    .replace(/\s+/g, "");
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey(
    "pkcs8", der.buffer as ArrayBuffer, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]
  );
  cachedKey = { pem, key };
  return key;
}

/** A fresh or cached ES256 JWT — Apple's `authorization: bearer` value. */
export async function signedAuthToken(config: ApnsConfig, now: number = Date.now()): Promise<string> {
  if (cachedToken && cachedToken.keyId === config.keyId
      && now - cachedToken.madeAt < TOKEN_LIFETIME_MS) {
    return cachedToken.jwt;
  }

  const encoder = new TextEncoder();
  const header = base64url(encoder.encode(JSON.stringify({ alg: "ES256", kid: config.keyId })));
  const payload = base64url(encoder.encode(JSON.stringify({
    iss: config.teamId, iat: Math.floor(now / 1000),
  })));
  const signingInput = `${header}.${payload}`;

  const key = await importPrivateKey(config.privateKeyPEM);
  // Web Crypto's ECDSA signature is already the raw R||S concatenation JWS expects —
  // no DER-to-raw conversion needed, unlike most other ECDSA APIs.
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" }, key, encoder.encode(signingInput)
  );

  const jwt = `${signingInput}.${base64url(signature)}`;
  cachedToken = { jwt, madeAt: now, keyId: config.keyId };
  return jwt;
}

/** Only exposed for tests — a fresh process/isolate should never reuse another one's key. */
export function _resetCaches(): void {
  cachedToken = null;
  cachedKey = null;
}

/** `undefined` unless a title or body was actually given — used only where an alert is
 * optional (the two Live Activity push types that can go out silent). The plain `alert`
 * push type below always gets one, defaulted, regardless. */
function optionalAlert(request: PushRequest): { title: string; body: string } | undefined {
  if (!request.title && !request.body) return undefined;
  return { title: request.title ?? "Overwatch Queue", body: request.body ?? "Status changed." };
}

function payloadFor(request: PushRequest): object {
  switch (request.pushType) {
    case "alert":
      return {
        aps: { alert: { title: request.title ?? "Overwatch Queue", body: request.body ?? "Status changed." },
              sound: "default" },
        sessionID: request.sessionId,
        sequence: request.sequence,
      };
    case "liveActivityStart": {
      const aps: Record<string, unknown> = {
        timestamp: request.timestamp, event: "start", "content-state": request.contentState,
        "attributes-type": "QueueActivityAttributes", attributes: request.attributes,
      };
      const alert = optionalAlert(request);
      if (alert) aps.alert = alert;
      return { aps };
    }
    case "liveActivityUpdate": {
      const aps: Record<string, unknown> = {
        timestamp: request.timestamp, event: "update", "content-state": request.contentState,
      };
      const alert = optionalAlert(request);
      if (alert) aps.alert = alert;
      if (request.staleDate !== undefined) aps["stale-date"] = request.staleDate;
      return { aps };
    }
    case "liveActivityEnd": {
      const aps: Record<string, unknown> = {
        timestamp: request.timestamp, event: "end", "content-state": request.contentState,
      };
      if (request.dismissalDate !== undefined) aps["dismissal-date"] = request.dismissalDate;
      return { aps };
    }
    case "background":
    default:
      return {
        aps: { "content-available": 1 },
        sessionID: request.sessionId,
        sequence: request.sequence,
      };
  }
}

export interface ApnsResult {
  status: number;
  body: string;
}

/** A Live Activity push always targets the iOS app specifically — the watch has no Live
 * Activities — under a topic Apple derives from the bundle ID, with `.start` appended
 * only for the push that creates the activity from nothing. */
function topicFor(config: ApnsConfig, request: PushRequest): string {
  if (LIVE_ACTIVITY_TYPES.includes(request.pushType)) {
    const suffix = request.pushType === "liveActivityStart" ? ".start" : "";
    return `${config.bundleIdIOS}.push-type.liveactivity${suffix}`;
  }
  return request.kind === "phone" ? config.bundleIdIOS : config.bundleIdWatch;
}

/** The value APNs actually wants in `apns-push-type` — all three Live Activity request
 * types collapse to Apple's one literal `liveactivity`. */
function apnsPushType(pushType: PushType): string {
  return LIVE_ACTIVITY_TYPES.includes(pushType) ? "liveactivity" : pushType;
}

/** Apple requires immediate (10) priority for every Live Activity push; background
 * wake-ups use 5 so as not to compete with more urgent traffic. */
function priorityFor(pushType: PushType): string {
  return pushType === "background" ? "5" : "10";
}

/** Sends one push and hands back Apple's raw status/body — the caller (the Worker's HTTP
 * handler) proxies this straight through, so the PC sees exactly what Apple said, the
 * same as if it had talked to Apple directly. */
export async function sendPush(config: ApnsConfig, request: PushRequest): Promise<ApnsResult> {
  const url = `${HOSTS[request.environment]}/3/device/${request.token}`;
  const token = await signedAuthToken(config);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "authorization": `bearer ${token}`,
      "apns-topic": topicFor(config, request),
      "apns-push-type": apnsPushType(request.pushType),
      "apns-priority": priorityFor(request.pushType),
      "content-type": "application/json",
    },
    body: JSON.stringify(payloadFor(request)),
  });
  const body = await response.text();
  return { status: response.status, body };
}
