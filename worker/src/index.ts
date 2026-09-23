import { DurableObject } from "cloudflare:workers";
import { alertMessage, androidMessage, FcmResult, liveActivityMessage, parseServiceAccount, send, wakeMessage } from "./fcm";
import {
  activityPayload, advance, androidPayload, foundBody, IDLE, nextStatus, parseReport,
  PLAYING_AFTER_SECONDS, Push, pushesFor, Status,
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

/**
 * A queue or a match the PC hasn't mentioned in this long is over as far as anyone here is
 * concerned: the PC was closed, went to sleep, or lost its network mid-queue, and without this
 * the phone would show a queue ticking up for good. Hours rather than minutes precisely so the
 * PC needs no heartbeat to hold it off -- it only ever speaks when something changes, and no
 * real queue or match goes three hours without changing. The same horizon as the activity's own
 * STALE_AFTER_SECONDS, which covers a phone this end push can't reach.
 */
const ABANDONED_AFTER_SECONDS = 3 * 3600;

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
      return { status: 200, body: await this.snapshot() };
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
        return { status: 200, body: await this.snapshot() };
      }
      if (input.kind === "watch") {
        const fcm = token(input.fcm, FCM_TOKEN);
        if (!fcm) return { status: 400, body: { error: "bad_token" } };
        await this.ctx.storage.put("watch", { fcm } satisfies Watch);
        return { status: 200, body: await this.snapshot() };
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
      return { status: 200, body: await this.snapshot() };
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
    return { status: 200, body: await this.snapshot() };
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
      if (status.state !== "idle" && now >= reportedAt + ABANDONED_AFTER_SECONDS) {
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

  /**
   * The two things the worker decides on its own: "Match found!" becoming "In a match" a minute
   * later, and giving up on a PC that stopped talking. Whichever comes first.
   */
  private async schedule(status: Status): Promise<void> {
    const now = Date.now();
    const times: number[] = [];
    if (status.state === "found" && status.foundAt !== null) times.push((status.foundAt + PLAYING_AFTER_SECONDS) * 1000);
    if (status.state !== "idle") {
      const reportedAt = (await this.ctx.storage.get<number>("reportedAt")) ?? now / 1000;
      times.push((reportedAt + ABANDONED_AFTER_SECONDS) * 1000);
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

  /**
   * What every reply says: the state, and who's paired. Registering or reporting already has the
   * pair open, so handing this back saves the caller a second request for it.
   */
  private async snapshot(): Promise<Record<string, unknown>> {
    return { status: await this.status(), ...(await this.paired()) };
  }

  /** Which devices have registered. A report carries these so the PC needs no second request. */
  private async paired(): Promise<Record<string, boolean>> {
    const phone = await this.phone();
    const watch = await this.ctx.storage.get<Watch>("watch");
    const android = await this.ctx.storage.get<AndroidDevice>("device:android");
    return {
      phonePaired: Boolean(phone.fcm),
      watchPaired: Boolean(watch?.fcm),
      androidPaired: Boolean(android?.fcm),
    };
  }

  /** Sends a push to every paired device. `status` is always the state after the change. */
  private async deliver(push: Push, status: Status, now: number): Promise<void> {
    await this.deliverAndroid(push, status, now);
    await this.deliverApple(push, status, now);
    await this.deliverWatch(push, status);
  }

  /**
   * The Watch has no Live Activity, so it's sent the state itself, silently, and its app shows
   * that instead of polling for it. An end that a start follows carries the same state the start
   * does, so only the start goes; a match alert reaches the Watch from the phone's own alert.
   */
  private async deliverWatch(push: Push, status: Status): Promise<void> {
    if (push.kind === "matchAlert" || (push.kind === "end" && status.state !== "idle")) return;
    try {
      const watch = await this.ctx.storage.get<Watch>("watch");
      if (!watch?.fcm) return;
      const result = await this.fcm(wakeMessage(watch.fcm, { status }));
      if (result && dead(result)) await this.ctx.storage.delete("watch");
    } catch (error) {
      console.log(`deliver ${push.kind} to watch failed: ${String(error)}`);
    }
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
        // Whatever alerts the phone -- the Live Activity's alert, or the banner below -- shows on
        // the Watch as well, so the Watch's own banner is only for a phone that can't be reached.
        const watch = phone.fcm ? undefined : await this.ctx.storage.get<Watch>("watch");
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

      const activityToken = push.kind === "start" ? phone.startToken : phone.updateToken;
      let result: FcmResult | null = null;
      if (activityToken) {
        const alert = push.kind === "start" || push.kind === "update" ? push.alert : undefined;
        const linger = push.kind === "end" ? push.linger ?? 0 : 0;
        const payload = activityPayload(push.kind, status, now, alert, linger);
        result = await this.fcm(liveActivityMessage(phone.fcm, activityToken, payload));
        console.log(`push ${push.kind}${alert ? ` (${alert})` : ""} -> ${tail(activityToken)}: `
          + (result ? `${result.status}${result.error ? ` ${result.error}` : ""}` : "unreachable"));
      } else {
        console.log(`push ${push.kind} has no ${push.kind === "start" ? "start" : "update"} token`);
      }

      if (push.kind === "end" || push.kind === "start") {
        delete phone.updateToken;
        // iOS can swap the push-to-start token once it has used it, and a new activity has a new
        // update token. Wake the app so it reports both instead of waiting until it's next opened.
        // This has to happen even when the push above never went: a token we don't have is exactly
        // the one we need back, and skipping the wake is how the phone got stuck on stale tokens.
        await this.fcm(wakeMessage(phone.fcm));
      }
      const tokenDead = result !== null && dead(result);
      if (tokenDead) {
        if (push.kind === "start") delete phone.startToken;
        else delete phone.updateToken;
      }
      await this.ctx.storage.put("phone", phone);
      // A found match is the one thing that can't just be dropped. If the running activity couldn't
      // take it, start a fresh one carrying the alert; if there's no start token either, the
      // matchAlert push that follows this one sends the plain banner.
      if (push.kind === "update" && push.alert === "found" && (!activityToken || tokenDead) && phone.startToken) {
        await this.deliverApple({ kind: "start", alert: "found" }, status, now);
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
