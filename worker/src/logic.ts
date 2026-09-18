export const STATES = ["idle", "queueing", "found"] as const;
export const MODES = ["quickPlay", "competitive", "arcade", "stadium", "mysteryHeroes", "custom"] as const;

export type QueueState = (typeof STATES)[number];
export type Mode = (typeof MODES)[number];

/** Exactly the Live Activity's ContentState on the phone. Times are unix seconds. */
export interface Status {
  state: QueueState;
  mode: Mode | null;
  startedAt: number | null;
  foundAt: number | null;
}

export interface Report {
  state: QueueState;
  mode: Mode | null;
  elapsed: number;
}

export type Push =
  | { kind: "start"; alert: "queue" | "found" }
  | { kind: "update"; alert?: "found" }
  | { kind: "end" }
  | { kind: "matchAlert" };

export const IDLE: Status = { state: "idle", mode: null, startedAt: null, foundAt: null };

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
  const { state, mode, elapsed } = body as Record<string, unknown>;
  if (!STATES.includes(state as QueueState)) return null;
  if (mode !== null && mode !== undefined && !MODES.includes(mode as Mode)) return null;
  const seconds = typeof elapsed === "number" && Number.isFinite(elapsed) ? elapsed : 0;
  return {
    state: state as QueueState,
    mode: (mode as Mode | undefined) ?? null,
    elapsed: Math.min(Math.max(seconds, 0), 6 * 3600),
  };
}

export function nextStatus(prev: Status, report: Report, now: number): Status {
  if (report.state === "idle") return IDLE;
  const wasActive = prev.state !== "idle" && prev.startedAt !== null;
  const startedAt = wasActive && !(prev.state === "found" && report.state === "queueing")
    ? prev.startedAt
    : Math.round(now - report.elapsed);
  if (report.state === "queueing") {
    return { state: "queueing", mode: report.mode ?? (prev.state === "queueing" ? prev.mode : null), startedAt, foundAt: null };
  }
  return {
    state: "found",
    mode: report.mode ?? prev.mode,
    startedAt,
    foundAt: prev.state === "found" && prev.foundAt !== null ? prev.foundAt : Math.round(now),
  };
}

export function pushesFor(prev: Status, next: Status): Push[] {
  if (prev.state === next.state && prev.mode === next.mode) return [];
  switch (next.state) {
    case "idle":
      return [{ kind: "end" }];
    case "queueing":
      if (prev.state === "queueing") return [{ kind: "update" }];
      if (prev.state === "found") return [{ kind: "end" }, { kind: "start", alert: "queue" }];
      return [{ kind: "start", alert: "queue" }];
    case "found":
      if (prev.state === "found") return [{ kind: "update" }];
      return [{ kind: "update", alert: "found" }, { kind: "matchAlert" }];
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
  if (event === "end") aps["dismissal-date"] = Math.floor(now);
  if (alert === "queue") {
    aps["alert"] = { title: "In queue", body: modeName(status.mode) };
  } else if (alert === "found") {
    aps["alert"] = { title: "Match found!", body: foundBody(status), sound: "match_found.caf" };
    aps["interruption-level"] = "time-sensitive";
  }
  return aps;
}
