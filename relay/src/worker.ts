/**
 * The one thing this service does: take a push request from a paired PC's own LAN
 * server, and forward it to Apple using the one Auth Key this Worker holds.
 *
 * That key never goes anywhere else — every PC that installs the app talks to this
 * Worker instead of to Apple directly, so nobody running the open-source server ever
 * needs (or can leak) the real Apple credential. See `README.md` for what that does and
 * doesn't protect against.
 */
import { sendPush, type ApnsConfig } from "./apns";
import { isRateLimited } from "./rateLimit";
import { constantTimeEqual, validatePushRequest } from "./validate";

export interface Env {
  APNS_TEAM_ID: string;
  APNS_KEY_ID: string;
  APNS_BUNDLE_ID_IOS: string;
  APNS_BUNDLE_ID_WATCH: string;
  APNS_PRIVATE_KEY: string;      // the .p8 contents, PEM, set with `wrangler secret put`
  RELAY_API_KEY: string;         // set with `wrangler secret put`
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function configFrom(env: Env): ApnsConfig {
  return {
    teamId: env.APNS_TEAM_ID,
    keyId: env.APNS_KEY_ID,
    bundleIdIOS: env.APNS_BUNDLE_ID_IOS,
    bundleIdWatch: env.APNS_BUNDLE_ID_WATCH,
    privateKeyPEM: env.APNS_PRIVATE_KEY,
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/v1/push") return json(404, { error: "not_found" });
    if (request.method !== "POST") return json(405, { error: "method_not_allowed" });

    const authorization = request.headers.get("authorization") ?? "";
    if (!constantTimeEqual(authorization, `Bearer ${env.RELAY_API_KEY}`)) {
      return json(401, { error: "unauthorized" });
    }

    const clientIP = request.headers.get("cf-connecting-ip") ?? "unknown";
    if (isRateLimited(clientIP)) {
      return json(429, { error: "rate_limited" });
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return json(400, { error: "invalid_json" });
    }

    const parsed = validatePushRequest(body);
    if (typeof parsed === "string") {
      return json(400, { error: parsed });
    }

    try {
      const result = await sendPush(configFrom(env), parsed);
      // Proxied verbatim: the PC's own `token_is_invalid` check reads this exactly as it
      // would read Apple's response if it were talking to Apple directly.
      return new Response(result.body, {
        status: result.status,
        headers: { "content-type": "application/json" },
      });
    } catch (error) {
      return json(502, { error: "apns_unreachable", detail: String(error) });
    }
  },
};
