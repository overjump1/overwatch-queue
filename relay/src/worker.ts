/**
 * The one thing this service does: take a Live Activity push from a paired PC's own LAN
 * server, and forward it to Firebase using the one service account this Worker holds.
 *
 * That credential never goes anywhere else — every PC that installs the app talks to this
 * Worker instead of to Firebase directly, so nobody running the open-source server ever
 * needs (or can leak) it. See `README.md` for what that does and doesn't protect against.
 */
import { parseServiceAccount, sendPush } from "./fcm";
import { isRateLimited } from "./rateLimit";
import { validatePushRequest } from "./validate";

export interface Env {
  FCM_SERVICE_ACCOUNT: string;   // the service-account JSON, set with `wrangler secret put`
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/v1/push") return json(404, { error: "not_found" });
    if (request.method !== "POST") return json(405, { error: "method_not_allowed" });

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
      const result = await sendPush(parseServiceAccount(env.FCM_SERVICE_ACCOUNT), parsed);
      // Proxied verbatim: the PC's own `token_is_invalid` check reads this exactly as it
      // would read Firebase's response if it were talking to Firebase directly.
      return new Response(result.body, {
        status: result.status,
        headers: { "content-type": "application/json" },
      });
    } catch (error) {
      return json(502, { error: "firebase_unreachable", detail: String(error) });
    }
  },
};
