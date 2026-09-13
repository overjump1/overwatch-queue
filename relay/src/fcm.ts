/**
 * Everything about talking to Firebase: minting an OAuth access token from the service
 * account and sending one Live Activity push.
 *
 * Pulled out of `worker.ts` so it has no dependency on the Workers `Request`/`Response`
 * types — it can be unit-tested with plain values (see `test/fcm.test.ts`).
 *
 * The message built here is exactly the one `server/owqserver/fcm.py` used to post to
 * Firebase itself; see that file for why Live Activity pushes go through Firebase at all
 * rather than straight to Apple.
 */

export interface ServiceAccount {
  project_id: string;
  client_email: string;
  private_key: string;
  token_uri?: string;
}

export type LiveActivityEvent = "start" | "update" | "end";

/** What a PC asks for, once `validate.ts` has checked it. */
export interface PushRequest {
  /** The phone's FCM registration token — Firebase's address for the device. */
  deviceToken: string;
  /** The ActivityKit token: push-to-start for `start`, per-activity otherwise. */
  liveActivityToken: string;
  aps: Record<string, unknown>;
}

export interface PushResult {
  status: number;
  body: string;
}

const SCOPE = "https://www.googleapis.com/auth/firebase.messaging";
const DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token";

// Apple requires immediate priority for every Live Activity push. Fixed here rather than
// taken from the request, so nothing can use this relay to send any other kind of push.
const LIVE_ACTIVITY_HEADERS = { "apns-priority": "10", "apns-push-type": "liveactivity" };

// Google's access tokens last an hour; refresh a little before that so a push never goes
// out carrying one that expires in flight.
const REFRESH_MARGIN_MS = 5 * 60 * 1000;

let cachedToken: { token: string; expiresAt: number; email: string } | null = null;
let cachedKey: { pem: string; key: CryptoKey } | null = null;

export function parseServiceAccount(json: string): ServiceAccount {
  const account = JSON.parse(json) as Partial<ServiceAccount>;
  if (!account.project_id || !account.client_email || !account.private_key) {
    throw new Error("FCM_SERVICE_ACCOUNT is missing project_id, client_email or private_key");
  }
  return account as ServiceAccount;
}

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
    "pkcs8", der.buffer as ArrayBuffer, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]
  );
  cachedKey = { pem, key };
  return key;
}

/** The RS256-signed assertion Google exchanges for an access token. */
export async function signedAssertion(account: ServiceAccount, now: number = Date.now()): Promise<string> {
  const encoder = new TextEncoder();
  const issuedAt = Math.floor(now / 1000);
  const header = base64url(encoder.encode(JSON.stringify({ alg: "RS256", typ: "JWT" })));
  const payload = base64url(encoder.encode(JSON.stringify({
    iss: account.client_email, scope: SCOPE, aud: account.token_uri ?? DEFAULT_TOKEN_URI,
    iat: issuedAt, exp: issuedAt + 3600,
  })));
  const signingInput = `${header}.${payload}`;
  const key = await importPrivateKey(account.private_key);
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, encoder.encode(signingInput));
  return `${signingInput}.${base64url(signature)}`;
}

/** A fresh or cached OAuth access token — Firebase's `Authorization: Bearer` value. */
export async function accessToken(account: ServiceAccount, now: number = Date.now()): Promise<string> {
  if (cachedToken && cachedToken.email === account.client_email
      && now < cachedToken.expiresAt - REFRESH_MARGIN_MS) {
    return cachedToken.token;
  }

  const response = await fetch(account.token_uri ?? DEFAULT_TOKEN_URI, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: await signedAssertion(account, now),
    }).toString(),
  });
  if (!response.ok) {
    throw new Error(`token exchange failed: ${response.status} ${await response.text()}`);
  }
  const { access_token, expires_in } = await response.json() as { access_token: string; expires_in: number };
  cachedToken = { token: access_token, expiresAt: now + expires_in * 1000, email: account.client_email };
  return access_token;
}

/** Only exposed for tests — a fresh isolate should never reuse another one's token. */
export function _resetCaches(): void {
  cachedToken = null;
  cachedKey = null;
}

export function messageFor(request: PushRequest): object {
  // Two tokens at once, and neither substitutes for the other: `token` is the device's
  // FCM registration, `live_activity_token` the card's. Swapping them is accepted with a
  // 200 that never arrives.
  return {
    message: {
      token: request.deviceToken,
      apns: {
        live_activity_token: request.liveActivityToken,
        headers: { ...LIVE_ACTIVITY_HEADERS },
        payload: { aps: request.aps },
      },
    },
  };
}

/** Sends one push and hands back Firebase's raw status/body — the Worker proxies it
 * straight through, so the PC reads an `UNREGISTERED` exactly as Firebase said it. */
export async function sendPush(account: ServiceAccount, request: PushRequest): Promise<PushResult> {
  const token = await accessToken(account);
  const response = await fetch(`https://fcm.googleapis.com/v1/projects/${account.project_id}/messages:send`, {
    method: "POST",
    headers: { "authorization": `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(messageFor(request)),
  });
  return { status: response.status, body: await response.text() };
}
