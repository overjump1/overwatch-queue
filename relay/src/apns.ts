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
export type PushType = "background" | "alert";

export interface PushRequest {
  kind: Kind;
  token: string;
  environment: Environment;
  pushType: PushType;
  sessionId: string;
  sequence: number;
  title?: string;
  body?: string;
}

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

function payloadFor(request: PushRequest): object {
  if (request.pushType === "alert") {
    return {
      aps: { alert: { title: request.title ?? "Overwatch Queue", body: request.body ?? "Status changed." },
            sound: "default" },
      sessionID: request.sessionId,
      sequence: request.sequence,
    };
  }
  return {
    aps: { "content-available": 1 },
    sessionID: request.sessionId,
    sequence: request.sequence,
  };
}

export interface ApnsResult {
  status: number;
  body: string;
}

/** Sends one push and hands back Apple's raw status/body — the caller (the Worker's HTTP
 * handler) proxies this straight through, so the PC sees exactly what Apple said, the
 * same as if it had talked to Apple directly. */
export async function sendPush(config: ApnsConfig, request: PushRequest): Promise<ApnsResult> {
  const bundleId = request.kind === "phone" ? config.bundleIdIOS : config.bundleIdWatch;
  const url = `${HOSTS[request.environment]}/3/device/${request.token}`;
  const token = await signedAuthToken(config);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "authorization": `bearer ${token}`,
      "apns-topic": bundleId,
      "apns-push-type": request.pushType,
      "apns-priority": request.pushType === "alert" ? "10" : "5",
      "content-type": "application/json",
    },
    body: JSON.stringify(payloadFor(request)),
  });
  const body = await response.text();
  return { status: response.status, body };
}
