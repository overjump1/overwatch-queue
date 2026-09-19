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

async function accessToken(account: ServiceAccount): Promise<string> {
  const now = Date.now();
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

/** A regular, time-sensitive alert notification. */
export function alertMessage(token: string, title: string, body: string): object {
  return {
    message: {
      token,
      apns: {
        headers: { "apns-priority": "10", "apns-push-type": "alert" },
        payload: {
          aps: {
            alert: { title, body },
            sound: "match_found.caf",
            "interruption-level": "time-sensitive",
          },
        },
      },
    },
  };
}

/** A high-priority data message for the Android app, which shows the notification itself. */
export function androidMessage(token: string, data: Record<string, string>): object {
  return { message: { token, android: { priority: "HIGH", ttl: "60s" }, data } };
}

export async function send(account: ServiceAccount, message: object): Promise<FcmResult> {
  const token = await accessToken(account);
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
  console.log(`fcm ${response.status} ${text.slice(0, 300)}`);
  return { ok: false, status: response.status, error };
}
