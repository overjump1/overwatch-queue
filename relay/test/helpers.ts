import type { ServiceAccount } from "../src/fcm";

export const DEVICE_TOKEN = "fGhIjKlMnOp:APA91bH-registration_token";
export const ACTIVITY_TOKEN = "a".repeat(64);

export function pemFrom(der: ArrayBuffer): string {
  const bytes = new Uint8Array(der);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const base64 = btoa(binary).match(/.{1,64}/g)!.join("\n");
  return `-----BEGIN PRIVATE KEY-----\n${base64}\n-----END PRIVATE KEY-----\n`;
}

export function base64urlDecode(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/")
    .padEnd(text.length + ((4 - (text.length % 4)) % 4), "=");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

/** A throwaway RSA service account, generated per test — never a real credential. */
export async function generateServiceAccount(): Promise<{ account: ServiceAccount; publicKey: CryptoKey }> {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true, ["sign", "verify"]
  ) as CryptoKeyPair;
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey) as ArrayBuffer;
  return {
    account: {
      project_id: "overwatch-queue",
      client_email: "relay@overwatch-queue.iam.gserviceaccount.com",
      private_key: pemFrom(pkcs8),
      token_uri: "https://oauth2.googleapis.com/token",
    },
    publicKey: pair.publicKey,
  };
}

/** Answers Google's token endpoint with a token, and anything else (Firebase) with `send`. */
export function fakeGoogle(send: () => Response = () => new Response("{}", { status: 200 })) {
  // `init` is typed loosely so tests can read the recorded call's headers and body back.
  return async (url: string, _init?: any) => {
    if (url.startsWith("https://oauth2.googleapis.com/")) {
      return new Response(JSON.stringify({ access_token: "ya29.fake", expires_in: 3600 }), { status: 200 });
    }
    return send();
  };
}
