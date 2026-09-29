export interface ServiceAccount {
  project_id: string;
  client_email: string;
  private_key: string;
  token_uri?: string;
}

export interface FcmResult {
  ok: boolean;
  status: number;
  /** Firebase's error status, e.g. "UNREGISTERED", when the push was rejected. */
  error?: string;
}

const SCOPE = "https://www.googleapis.com/auth/firebase.messaging";
const DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token";
const REFRESH_MARGIN_MS = 5 * 60 * 1000;

export interface AccessToken {
  token: string;
  /** Milliseconds. */
  expiresAt: number;
  email: string;
}

/**
 * Somewhere to keep the access token that outlives this isolate. A Durable Object that sat out a
 * long, quiet queue has usually been evicted by the time the match is found, and without this the
 * match-found push would first have to sign a JWT and trade it with Google for a new token.
 */
export interface TokenStore {
  get(): Promise<AccessToken | undefined>;
  put(token: AccessToken): Promise<void>;
}

let cachedToken: AccessToken | null = null;
/** The exchange under way, if any, and whose token it's for. */
let pendingToken: { email: string; token: Promise<AccessToken> } | null = null;
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
    "pkcs8", der.buffer as ArrayBuffer, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"],
  );
  cachedKey = { pem, key };
  return key;
}

async function signedAssertion(account: ServiceAccount, now: number): Promise<string> {
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

function usable(token: AccessToken | null | undefined, account: ServiceAccount, now: number): token is AccessToken {
  return !!token && token.email === account.client_email && now < token.expiresAt - REFRESH_MARGIN_MS;
}

async function accessToken(account: ServiceAccount, store?: TokenStore): Promise<string> {
  const now = Date.now();
  if (usable(cachedToken, account, now)) return cachedToken.token;
  const stored = await store?.get().catch(() => undefined);
  if (usable(stored, account, now)) {
    cachedToken = stored;
    return stored.token;
  }
  // Pushes go out several at once, and on a cold start they'd each trade for a token of their own.
  if (pendingToken?.email !== account.client_email) {
    const exchanging = exchange(account, now).finally(() => {
      if (pendingToken?.token === exchanging) pendingToken = null;
    });
    pendingToken = { email: account.client_email, token: exchanging };
  }
  const fresh = await pendingToken.token;
  cachedToken = fresh;
  await store?.put(fresh).catch(() => undefined);
  return fresh.token;
}

async function exchange(account: ServiceAccount, now: number): Promise<AccessToken> {
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
  return { token: access_token, expiresAt: now + expires_in * 1000, email: account.client_email };
}

/** A Live Activity push. `token` is the device's FCM token, `activityToken` the ActivityKit one. */
export function liveActivityMessage(token: string, activityToken: string, aps: object): object {
  return {
    message: {
      token,
      apns: {
        live_activity_token: activityToken,
        headers: { "apns-priority": "10", "apns-push-type": "liveactivity" },
        payload: { aps },
      },
    },
  };
}

export interface AlertOptions {
  /**
   * `apns-collapse-id`, which iOS and watchOS also take as the notification's identifier. The phone
   * and the Watch getting the same one is what lets the Watch alert at once: with an app of its own
   * on the Watch, watchOS holds an alert until the phone's copy shows up, to tell whether they're
   * the same notification, and with no matching copy it only alerts after a timeout.
   */
  collapseId?: string;
  /** Unix seconds after which Apple drops the alert instead of delivering it late. */
  expiresAt?: number;
  /** Custom keys next to `aps`, for the app to read. */
  data?: Record<string, unknown>;
}

/** A regular, time-sensitive alert notification. */
export function alertMessage(token: string, title: string, body: string, options: AlertOptions = {}): object {
  const headers: Record<string, string> = { "apns-priority": "10", "apns-push-type": "alert" };
  if (options.collapseId) headers["apns-collapse-id"] = options.collapseId;
  if (options.expiresAt !== undefined) headers["apns-expiration"] = String(Math.floor(options.expiresAt));
  const aps: Record<string, unknown> = {
    alert: { title, body },
    sound: "match_found.caf",
    "interruption-level": "time-sensitive",
  };
  return { message: { token, apns: { headers, payload: { ...options.data, aps } } } };
}

/**
 * A silent push that wakes the app in the background: the phone to re-register its current tokens,
 * the Watch with `data` carrying the state. Apple only takes priority 5 for these.
 */
export function wakeMessage(token: string, data: object = {}): object {
  return {
    message: {
      token,
      apns: {
        headers: { "apns-priority": "5", "apns-push-type": "background" },
        payload: { aps: { "content-available": 1 }, ...data },
      },
    },
  };
}

/**
 * A high-priority data message for the Android app, which shows the notification itself.
 * An alert is pointless once it's late, but a quiet update or end still has to land, or the
 * phone keeps showing a queue that's over. The app drops pushes older than the last one it got.
 */
export function androidMessage(token: string, data: Record<string, string>): object {
  const ttl = data.alert ? "60s" : "3600s";
  return { message: { token, android: { priority: "HIGH", ttl }, data } };
}

export async function send(account: ServiceAccount, message: object, store?: TokenStore): Promise<FcmResult> {
  const token = await accessToken(account, store);
  const response = await fetch(`https://fcm.googleapis.com/v1/projects/${account.project_id}/messages:send`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(message),
  });
  const text = await response.text();
  if (response.ok) return { ok: true, status: response.status };
  let error: string | undefined;
  try {
    const parsed = JSON.parse(text) as { error?: { status?: string; details?: { errorCode?: string }[] } };
    error = parsed.error?.details?.find((d) => d.errorCode)?.errorCode ?? parsed.error?.status;
  } catch {
    error = undefined;
  }
  // Compacted, not cut short: APNs' own reason (e.g. BadDeviceToken) is at the end.
  let compact = text;
  try {
    compact = JSON.stringify(JSON.parse(text));
  } catch {
    // not JSON, log it as is
  }
  console.log(`fcm ${response.status} ${compact.slice(0, 2000)}`);
  return { ok: false, status: response.status, error };
}
