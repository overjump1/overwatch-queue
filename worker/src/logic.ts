export const STATES = ["idle", "queueing", "found", "playing"] as const;
/** What the PC can report. "playing" is only ever derived here, from how long ago the match was found. */
export const REPORTED_STATES = ["idle", "queueing", "found"] as const;
export const MODES = ["quickPlay", "competitive", "arcade", "stadium", "mysteryHeroes", "custom"] as const;

export type QueueState = (typeof STATES)[number];
export type ReportedState = (typeof REPORTED_STATES)[number];
export type Mode = (typeof MODES)[number];

/** Exactly the Live Activity's ContentState on the phone. Times are unix seconds. */
export interface Status {
  state: QueueState;
  mode: Mode | null;
  startedAt: number | null;
  foundAt: number | null;
}

export interface Report {
  state: ReportedState;
  mode: Mode | null;
  /** Seconds queued; once found, the final wait. */
  elapsed: number;
  /** Seconds since the match was found. Older PC apps don't send it, which reads as 0. */
  sinceFound: number;
}

export type Push =
  | { kind: "start"; alert?: "queue" | "found" }
  | { kind: "update"; alert?: "found" }
  /** `linger`: seconds the ended activity stays on the lock screen showing "Not in queue". */
  | { kind: "end"; linger?: number }
  | { kind: "matchAlert" };

export const IDLE: Status = { state: "idle", mode: null, startedAt: null, foundAt: null };

/** "Match found!" turns into "In a match" (good luck, have fun) this long after the match is found. */
export const PLAYING_AFTER_SECONDS = 60;
/** How long the "Not in queue" Live Activity stays on the lock screen after a match... */
export const AFTER_MATCH_LINGER_SECONDS = 10 * 60;
/** ...and after the queue was cancelled, just long enough to confirm it. */
export const AFTER_CANCEL_LINGER_SECONDS = 60;
const MAX_SECONDS = 6 * 3600;

export function inMatch(status: Status): boolean {
  return status.state === "found" || status.state === "playing";
}

function matchState(foundAt: number, now: number): "found" | "playing" {
  return now >= foundAt + PLAYING_AFTER_SECONDS ? "playing" : "found";
}

const MODE_NAMES: Record<Mode, string> = {
  quickPlay: "Quick Play",
  competitive: "Competitive",
  arcade: "Arcade",
  stadium: "Stadium",
  mysteryHeroes: "Mystery Heroes",
  custom: "Custom Game",
};

export function modeName(mode: Mode | null): string {
  return mode ? MODE_NAMES[mode] : "Overwatch";
}

export function parseReport(body: unknown): Report | null {
  if (typeof body !== "object" || body === null) return null;
  const { state, mode, elapsed, sinceFound } = body as Record<string, unknown>;
  if (!REPORTED_STATES.includes(state as ReportedState)) return null;
  if (mode !== null && mode !== undefined && !MODES.includes(mode as Mode)) return null;
  return {
    state: state as ReportedState,
    mode: (mode as Mode | undefined) ?? null,
    elapsed: seconds(elapsed),
    sinceFound: seconds(sinceFound),
  };
}

function seconds(value: unknown): number {
  const number = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return Math.min(Math.max(number, 0), MAX_SECONDS);
}

export function nextStatus(prev: Status, report: Report, now: number): Status {
  if (report.state === "idle") return IDLE;
  if (report.state === "queueing") {
    const continuing = prev.state === "queueing" && prev.startedAt !== null;
    return {
      state: "queueing",
      mode: report.mode ?? (continuing ? prev.mode : null),
      startedAt: continuing ? prev.startedAt : Math.round(now - report.elapsed),
      foundAt: null,
    };
  }
  // The same match keeps its found time however often the PC repeats itself, so it's
  // only ever announced once.
  if (inMatch(prev) && prev.foundAt !== null) {
    return { ...prev, state: matchState(prev.foundAt, now), mode: report.mode ?? prev.mode };
  }
  const foundAt = Math.round(now - report.sinceFound);
  const startedAt = prev.state === "queueing" && prev.startedAt !== null
    ? prev.startedAt
    : Math.round(foundAt - report.elapsed);
  return { state: matchState(foundAt, now), mode: report.mode ?? prev.mode, startedAt, foundAt };
}

/** Advances a found match to "playing" once enough time has passed; anything else is returned as is. */
export function advance(status: Status, now: number): Status {
  if (!inMatch(status) || status.foundAt === null) return status;
  const state = matchState(status.foundAt, now);
  return state === status.state ? status : { ...status, state };
}

/** Only a queue starting and a match being found make a sound. Everything else is a quiet update. */
export function pushesFor(prev: Status, next: Status): Push[] {
  if (prev.state === next.state && prev.mode === next.mode) return [];
  switch (next.state) {
    case "idle":
      if (inMatch(prev)) return [{ kind: "end", linger: AFTER_MATCH_LINGER_SECONDS }];
      return [{ kind: "end", linger: AFTER_CANCEL_LINGER_SECONDS }];
    case "queueing":
      if (prev.state === "queueing") return [{ kind: "update" }];
      if (inMatch(prev)) return [{ kind: "end" }, { kind: "start", alert: "queue" }];
      return [{ kind: "start", alert: "queue" }];
    case "found":
      if (prev.state === "found") return [{ kind: "update" }];
      return [{ kind: "update", alert: "found" }, { kind: "matchAlert" }];
    case "playing":
      // A match the worker only heard about late (the PC was offline when it was found)
      // shows up quietly instead of alerting minutes into the game.
      if (prev.state === "idle") return [{ kind: "start" }];
      return [{ kind: "update" }];
  }
}

function clock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function foundBody(status: Status): string {
  const waited = status.startedAt !== null && status.foundAt !== null ? status.foundAt - status.startedAt : null;
  return waited !== null ? `${modeName(status.mode)} · waited ${clock(waited)}` : modeName(status.mode);
}

export function activityPayload(
  event: "start" | "update" | "end",
  status: Status,
  now: number,
  alert?: "queue" | "found",
  linger = 0,
): Record<string, unknown> {
  const aps: Record<string, unknown> = {
    timestamp: Math.floor(now),
    event,
    "content-state": status,
  };
  if (event === "start") {
    aps["attributes-type"] = "QueueActivityAttributes";
    aps["attributes"] = {};
  }
  if (event === "end") aps["dismissal-date"] = Math.floor(now) + linger;
  if (alert === "queue") {
    aps["alert"] = { title: "In queue", body: modeName(status.mode) };
  } else if (alert === "found") {
    aps["alert"] = { title: "Match found!", body: foundBody(status), sound: "match_found.caf" };
    aps["interruption-level"] = "time-sensitive";
  }
  return aps;
}

/**
 * The FCM data message an Android device gets for a push. Android has no Live Activity, so the app
 * draws its own ongoing notification from `status`. FCM data values have to be strings.
 * Returns null for a push Android doesn't need: the found `update` already carries the match alert.
 */
export function androidPayload(push: Push, status: Status, now: number): Record<string, string> | null {
  if (push.kind === "matchAlert") return null;
  const data: Record<string, string> = {
    event: push.kind,
    status: JSON.stringify(status),
    sentAt: String(Math.floor(now)),
  };
  if (push.kind === "end") data.linger = String(push.linger ?? 0);
  else if (push.alert) data.alert = push.alert;
  return data;
}
