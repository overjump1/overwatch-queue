import { DurableObject } from "cloudflare:workers";
import { alertMessage, androidMessage, FcmResult, liveActivityMessage, parseServiceAccount, send, wakeMessage } from "./fcm";
import {
  activityPayload, advance, androidPayload, foundBody, IDLE, nextStatus, parseReport, PLAYING_AFTER_SECONDS, Push, pushesFor, Status,
} from "./logic";

export interface Env {
  PAIR: DurableObjectNamespace<Pair>;
  FCM_SERVICE_ACCOUNT: string;
}

interface Phone {
  fcm?: string;
  startToken?: string;
  updateToken?: string;
  activitiesEnabled?: boolean;
}

interface Watch {
  fcm?: string;
}

/**
 * Android devices, each stored under `device:<kind>` with its FCM token. They all get every state
 * push as a data message. A Wear OS app would register as a new kind added here.
 */
const ANDROID_KINDS = ["android"] as const;
type AndroidKind = (typeof ANDROID_KINDS)[number];

interface AndroidDevice {
  fcm: string;
}

type Reply = { status: number; body: unknown };

const PC_SILENT_SECONDS = 180;

const PAIR_ID = /^[0-9a-f]{32}$/;
const FCM_TOKEN = /^[A-Za-z0-9_:-]{20,4096}$/;
const ACTIVITY_TOKEN = /^[0-9a-fA-F]{32,400}$/;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function token(value: unknown, pattern: RegExp): string | undefined {
  return typeof value === "string" && pattern.test(value) ? value : undefined;
}

export class Pair extends DurableObject<Env> {
  private chain: Promise<unknown> = Promise.resolve();
  /** Milliseconds; the last Android push's sentAt. */
  private androidSentAt = 0;

  private serial<T>(work: () => Promise<T>): Promise<T> {
    const run = this.chain.then(work, work);
    this.chain = run.catch(() => undefined);
    return run;
  }

  async report(body: unknown): Promise<Reply> {
    return this.serial(async () => {
      if (await this.ctx.storage.get("deleted")) return { status: 410, body: { error: "reset" } };
      const report = parseReport(body);
      if (!report) return { status: 400, body: { error: "bad_state" } };
      const now = Date.now() / 1000;
      const prev = await this.status();
      const next = nextStatus(prev, report, now);
      await this.ctx.storage.put("status", next);
      await this.ctx.storage.put("reportedAt", now);
      const pushes = pushesFor(prev, next);
      for (const push of pushes) await this.deliver(push, next, now);
      await this.schedule(next);
      return { status: 200, body: { status: next } };
    });
  }

  async register(body: unknown): Promise<Reply> {
    return this.serial(async () => {
      if (await this.ctx.storage.get("deleted")) return { status: 410, body: { error: "reset" } };
      if (typeof body !== "object" || body === null) return { status: 400, body: { error: "bad_body" } };
      const input = body as Record<string, unknown>;
      if (ANDROID_KINDS.includes(input.kind as AndroidKind)) {
        const fcm = token(input.fcm, FCM_TOKEN);
        if (!fcm) return { status: 400, body: { error: "bad_token" } };
        await this.ctx.storage.put(`device:${input.kind}`, { fcm } satisfies AndroidDevice);
        return { status: 200, body: {} };
      }
      if (input.kind === "watch") {
        const fcm = token(input.fcm, FCM_TOKEN);
        if (!fcm) return { status: 400, body: { error: "bad_token" } };
        await this.ctx.storage.put("watch", { fcm } satisfies Watch);
        return { status: 200, body: {} };
      }
      if (input.kind !== "phone") return { status: 400, body: { error: "bad_kind" } };
      const phone = await this.phone();
      const fcm = token(input.fcm, FCM_TOKEN);
      const startToken = token(input.startToken, ACTIVITY_TOKEN);
      const updateToken = token(input.updateToken, ACTIVITY_TOKEN);
      const newStartToken = startToken !== undefined && startToken !== phone.startToken;
      if (fcm) phone.fcm = fcm;
      if (startToken) phone.startToken = startToken;
      if (updateToken) phone.updateToken = updateToken;
      if (typeof input.activitiesEnabled === "boolean") phone.activitiesEnabled = input.activitiesEnabled;
      await this.ctx.storage.put("phone", phone);
      console.log(`register fcm=${tail(fcm)} start=${tail(startToken)}${newStartToken ? " (new)" : ""}`
        + ` update=${tail(updateToken)} running=${String(input.activityRunning)}`);

      const status = await this.status();
      const now = Date.now() / 1000;
      if (updateToken && status.state !== "idle") {
        await this.deliverApple({ kind: "update" }, status, now);
      } else if (newStartToken && status.state !== "idle" && !phone.updateToken && input.activityRunning === false) {
        // The start we sent went to a push-to-start token iOS had already replaced, so it
        // showed as a plain banner at most. Now that we have the current one, start it for real.
        await this.deliverApple({ kind: "start", alert: startAlert(status) }, status, now);
      }
      return { status: 200, body: {} };
    });
  }

  /**
   * A device unpairing. Only that device's tokens go: the pairing itself stays, so the other
   * phones on it carry on and the PC's code still works. The Watch goes with the iPhone, since
   * it's paired through the iPhone in the first place.
   */
  async forget(kind: string): Promise<Reply> {
    return this.serial(async () => {
      if (await this.ctx.storage.get("deleted")) return { status: 410, body: { error: "reset" } };
      if (kind === "phone") await this.ctx.storage.delete(["phone", "watch"]);
      else if (kind === "watch") await this.ctx.storage.delete("watch");
      else if (ANDROID_KINDS.includes(kind as AndroidKind)) await this.ctx.storage.delete(`device:${kind}`);
      else return { status: 400, body: { error: "bad_kind" } };
      return { status: 200, body: {} };
    });
  }

  async read(): Promise<Reply> {
    if (await this.ctx.storage.get("deleted")) return { status: 410, body: { error: "reset" } };
    const phone = await this.phone();
    const watch = await this.ctx.storage.get<Watch>("watch");
    const android = await this.ctx.storage.get<AndroidDevice>("device:android");
    return {
      status: 200,
      body: {
        status: await this.status(),
        phonePaired: Boolean(phone.fcm),
        watchPaired: Boolean(watch?.fcm),
        androidPaired: Boolean(android?.fcm),
      },
    };
  }

  async reset(): Promise<Reply> {
    return this.serial(async () => {
      await this.deliver({ kind: "end" }, IDLE, Date.now() / 1000);
      await this.ctx.storage.deleteAlarm();
      await this.ctx.storage.deleteAll();
      await this.ctx.storage.put("deleted", true);
      return { status: 200, body: {} };
    });
  }

  async alarm(): Promise<void> {
    await this.serial(async () => {
      if (await this.ctx.storage.get("deleted")) return;
      const now = Date.now() / 1000;
      const status = await this.status();
      const reportedAt = (await this.ctx.storage.get<number>("reportedAt")) ?? now;
      if (status.state !== "idle" && now >= reportedAt + PC_SILENT_SECONDS) {
        await this.ctx.storage.put("status", IDLE);
        await this.deliver({ kind: "end" }, IDLE, now);
        return;
      }
      const advanced = advance(status, now);
      if (advanced !== status) {
        await this.ctx.storage.put("status", advanced);
        for (const push of pushesFor(status, advanced)) await this.deliver(push, advanced, now);
      }
      await this.schedule(advanced);
    });
  }

  private async schedule(status: Status): Promise<void> {
    const now = Date.now();
    const times: number[] = [];
    if (status.state === "found" && status.foundAt !== null) times.push((status.foundAt + PLAYING_AFTER_SECONDS) * 1000);
    if (status.state !== "idle") {
      const reportedAt = (await this.ctx.storage.get<number>("reportedAt")) ?? now / 1000;
      times.push((reportedAt + PC_SILENT_SECONDS) * 1000);
    }
    if (times.length) await this.ctx.storage.setAlarm(Math.max(Math.min(...times), now + 1000));
    else await this.ctx.storage.deleteAlarm();
  }

  private async status(): Promise<Status> {
    return (await this.ctx.storage.get<Status>("status")) ?? IDLE;
  }

  private async phone(): Promise<Phone> {
    return (await this.ctx.storage.get<Phone>("phone")) ?? {};
  }

  /** Sends a push to every paired device. */
  private async deliver(push: Push, status: Status, now: number): Promise<void> {
    await this.deliverAndroid(push, status, now);
    await this.deliverApple(push, status, now);
  }

  private async deliverAndroid(push: Push, status: Status, now: number): Promise<void> {
    // Strictly increasing, so the phone can tell which of two pushes sent back to back (end, then
    // start) is newer. Workers' clock only moves on I/O, so Date.now() alone can repeat.
    this.androidSentAt = Math.max(now * 1000, Date.now(), this.androidSentAt + 1);
    const data = androidPayload(push, status, this.androidSentAt / 1000);
    if (!data) return;
    for (const kind of ANDROID_KINDS) {
      try {
        const device = await this.ctx.storage.get<AndroidDevice>(`device:${kind}`);
        if (!device) continue;
        const result = await this.fcm(androidMessage(device.fcm, data));
        if (result && dead(result)) await this.ctx.storage.delete(`device:${kind}`);
      } catch (error) {
        console.log(`deliver ${push.kind} to ${kind} failed: ${String(error)}`);
      }
    }
  }

  /** The iPhone's Live Activity and alerts, and the Watch's alert. */
  private async deliverApple(push: Push, status: Status, now: number): Promise<void> {
    const phone = await this.phone();
    try {
      if (push.kind === "matchAlert") {
        const watch = await this.ctx.storage.get<Watch>("watch");
        if (watch?.fcm) {
          const result = await this.fcm(alertMessage(watch.fcm, "Match found!", foundBody(status)));
          if (result && dead(result)) await this.ctx.storage.delete("watch");
        }
        const noActivity = phone.activitiesEnabled === false || (!phone.startToken && !phone.updateToken);
        if (phone.fcm && noActivity) {
          await this.fcm(alertMessage(phone.fcm, "Match found!", foundBody(status)));
        }
        return;
      }
      if (!phone.fcm) return;

      if (push.kind === "update" && !phone.updateToken) {
        if (push.alert === "found") await this.deliverApple({ kind: "start", alert: "found" }, status, now);
        return;
      }
      if (push.kind === "end" && !phone.updateToken) return;
      if (push.kind === "start" && !phone.startToken) return;

      const activityToken = push.kind === "start" ? phone.startToken! : phone.updateToken!;
      const alert = push.kind === "start" || push.kind === "update" ? push.alert : undefined;
      const linger = push.kind === "end" ? push.linger ?? 0 : 0;
      const payload = activityPayload(push.kind, status, now, alert, linger);
      const result = await this.fcm(liveActivityMessage(phone.fcm, activityToken, payload));
      console.log(`push ${push.kind}${alert ? ` (${alert})` : ""} -> ${tail(activityToken)}: `
        + (result ? `${result.status}${result.error ? ` ${result.error}` : ""}` : "unreachable"));

      if (push.kind === "end" || push.kind === "start") {
        delete phone.updateToken;
        // iOS can swap the push-to-start token once it has used it, and a new activity has a new
        // update token. Wake the app so it reports both instead of waiting until it's next opened.
        await this.fcm(wakeMessage(phone.fcm));
      }
      const tokenDead = result !== null && dead(result);
      if (tokenDead) {
        if (push.kind === "start") delete phone.startToken;
        else delete phone.updateToken;
      }
      await this.ctx.storage.put("phone", phone);
      if (tokenDead && push.kind === "update" && push.alert === "found") {
        if (phone.startToken) await this.deliverApple({ kind: "start", alert: "found" }, status, now);
        else await this.fcm(alertMessage(phone.fcm, "Match found!", foundBody(status)));
      }
    } catch (error) {
      console.log(`deliver ${push.kind} failed: ${String(error)}`);
    }
  }

  private async fcm(message: object): Promise<FcmResult | null> {
    try {
      return await send(parseServiceAccount(this.env.FCM_SERVICE_ACCOUNT), message);
    } catch (error) {
      console.log(`fcm unreachable: ${String(error)}`);
      return null;
    }
  }
}

function startAlert(status: Status): "queue" | "found" | "playing" {
  return status.state === "queueing" ? "queue" : status.state === "found" ? "found" : "playing";
}

/** The end of a token, enough to tell tokens apart in the logs. */
function tail(value: string | undefined): string {
  return value ? `…${value.slice(-8)}` : "-";
}

function dead(result: FcmResult): boolean {
  return !result.ok && (result.status === 400 || result.status === 404);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const match = url.pathname.match(/^\/v1\/pair\/([^/]+)(\/state|\/device)?$/);
    if (!match) return json(404, { error: "not_found" });
    const [, id, action] = match;
    if (!PAIR_ID.test(id)) return json(400, { error: "bad_pair_id" });
    const pair = env.PAIR.get(env.PAIR.idFromName(id));

    let body: unknown = null;
    if (request.method === "POST") {
      try {
        body = await request.json();
      } catch {
        return json(400, { error: "invalid_json" });
      }
    }

    let reply: Reply;
    if (action === "/state" && request.method === "POST") reply = await pair.report(body);
    else if (action === "/state" && request.method === "GET") reply = await pair.read();
    else if (action === "/device" && request.method === "POST") reply = await pair.register(body);
    else if (action === "/device" && request.method === "DELETE") {
      reply = await pair.forget(url.searchParams.get("kind") ?? "");
    }
    else if (!action && request.method === "DELETE") reply = await pair.reset();
    else return json(405, { error: "method_not_allowed" });
    return json(reply.status, reply.body);
  },
};
