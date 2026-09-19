import { activityPayload, advance, androidPayload, IDLE, nextStatus, parseReport, pushesFor, Report, Status } from "../src/logic";

const report = (state: Report["state"], elapsed = 0, sinceFound = 0, mode: Report["mode"] = "competitive"): Report =>
  ({ state, mode, elapsed, sinceFound });
const queueing = (mode: Status["mode"] = "competitive"): Status => nextStatus(IDLE, report("queueing", 5, 0, mode), 1000);
const found = (): Status => nextStatus(queueing(), report("found", 300), 1300);

describe("transitions", () => {
  it("starts the activity when a queue begins", () => {
    const next = queueing();
    expect(next).toEqual({ state: "queueing", mode: "competitive", startedAt: 995, foundAt: null });
    expect(pushesFor(IDLE, next)).toEqual([{ kind: "start", alert: "queue" }]);
  });

  it("sends nothing for a repeated report and keeps the start time", () => {
    const first = queueing();
    const again = nextStatus(first, report("queueing"), 1100);
    expect(again.startedAt).toBe(995);
    expect(pushesFor(first, again)).toEqual([]);
  });

  it("alerts on match found and keeps the queue start", () => {
    const first = queueing();
    const next = found();
    expect(next).toEqual({ state: "found", mode: "competitive", startedAt: 995, foundAt: 1300 });
    expect(pushesFor(first, next)).toEqual([{ kind: "update", alert: "found" }, { kind: "matchAlert" }]);
  });

  it("turns into a quiet 'in a match' a minute after the match is found", () => {
    const match = found();
    expect(advance(match, 1359)).toBe(match);
    const playing = advance(match, 1360);
    expect(playing).toEqual({ ...match, state: "playing" });
    expect(pushesFor(match, playing)).toEqual([{ kind: "update" }]);
  });

  it("never announces the same match twice, however long it lasts", () => {
    let status = found();
    for (let now = 1360; now < 1300 + 3600; now += 60) {
      const next = nextStatus(status, report("found", 300, now - 1300), now);
      expect(next.foundAt).toBe(1300);
      expect(pushesFor(status, next).filter((push) => "alert" in push && push.alert)).toEqual([]);
      status = next;
    }
    expect(status.state).toBe("playing");
  });

  it("picks up a match it heard about late with a silent banner", () => {
    const late = nextStatus(IDLE, report("found", 120, 600), 5000);
    expect(late).toEqual({ state: "playing", mode: "competitive", startedAt: 4280, foundAt: 4400 });
    expect(pushesFor(IDLE, late)).toEqual([{ kind: "start", alert: "playing" }]);
  });

  it("quietly confirms a cancelled queue for a minute", () => {
    const first = queueing();
    const cancelled = nextStatus(first, report("idle"), 1010);
    expect(cancelled).toEqual(IDLE);
    expect(pushesFor(first, cancelled)).toEqual([{ kind: "end", linger: 60 }]);
  });

  it("starts a fresh queue after a cancel", () => {
    const cancelled = nextStatus(queueing(), report("idle"), 1010);
    const again = nextStatus(cancelled, report("queueing", 2), 1100);
    expect(again.startedAt).toBe(1098);
    expect(pushesFor(cancelled, again)).toEqual([{ kind: "start", alert: "queue" }]);
  });

  it("leaves 'Not in queue' up for a while when a match ends", () => {
    const playing = advance(found(), 2000);
    expect(pushesFor(playing, nextStatus(playing, report("idle"), 2500))).toEqual([{ kind: "end", linger: 600 }]);
  });

  it("leaves 'Not in queue' up when a match ends within its first minute", () => {
    const match = found();
    expect(pushesFor(match, nextStatus(match, report("idle"), 1320))).toEqual([{ kind: "end", linger: 600 }]);
  });

  it("restarts after a found match goes back to queue", () => {
    const again = nextStatus(found(), report("queueing"), 1400);
    expect(again.startedAt).toBe(1400);
    expect(pushesFor(found(), again)).toEqual([{ kind: "end" }, { kind: "start", alert: "queue" }]);
  });

  it("updates quietly on a mode change", () => {
    const first = queueing("quickPlay");
    const next = nextStatus(first, report("queueing"), 1010);
    expect(pushesFor(first, next)).toEqual([{ kind: "update" }]);
  });
});

describe("reports", () => {
  it("rejects 'playing', which only the worker decides", () => {
    expect(parseReport({ state: "playing", mode: null, elapsed: 0 })).toBeNull();
  });

  it("treats a missing sinceFound as a match found just now", () => {
    expect(parseReport({ state: "found", mode: "arcade", elapsed: 12 })).toEqual(
      { state: "found", mode: "arcade", elapsed: 12, sinceFound: 0 });
  });
});

describe("payload", () => {
  it("builds a found alert with sound", () => {
    const match: Status = { state: "found", mode: "quickPlay", startedAt: 1000, foundAt: 1125 };
    const aps = activityPayload("update", match, 1125, "found");
    expect(aps.alert).toEqual({ title: "Match found!", body: "Quick Play · waited 2:05", sound: "match_found.caf" });
    expect(aps["interruption-level"]).toBe("time-sensitive");
    expect(aps["content-state"]).toEqual(match);
  });

  it("builds a silent banner for a match picked up late", () => {
    const aps = activityPayload("start", advance(found(), 2000), 2000, "playing");
    expect(aps.alert).toEqual({ title: "In a match", body: "Competitive" });
    expect(aps["interruption-level"]).toBeUndefined();
  });

  it("sends quiet updates without an alert", () => {
    const aps = activityPayload("update", advance(found(), 2000), 2000);
    expect(aps.alert).toBeUndefined();
    expect(aps["interruption-level"]).toBeUndefined();
  });

  it("names the attributes type on start", () => {
    const aps = activityPayload("start", queueing(), 1000, "queue");
    expect(aps["attributes-type"]).toBe("QueueActivityAttributes");
    expect(aps.attributes).toEqual({});
  });

  it("keeps an ended activity on screen for its linger time", () => {
    expect(activityPayload("end", IDLE, 2000)["dismissal-date"]).toBe(2000);
    expect(activityPayload("end", IDLE, 2000, undefined, 600)["dismissal-date"]).toBe(2600);
    expect(activityPayload("end", IDLE, 2000)["content-state"]).toEqual(IDLE);
  });
});

describe("android payload", () => {
  it("carries the status as JSON and the queue alert on start", () => {
    const status = queueing();
    expect(androidPayload({ kind: "start", alert: "queue" }, status, 1000.7)).toEqual(
      { event: "start", status: JSON.stringify(status), sentAt: "1000700", alert: "queue" });
  });

  it("carries the found alert on the match update", () => {
    const data = androidPayload({ kind: "update", alert: "found" }, found(), 1300);
    expect(data?.alert).toBe("found");
    expect(JSON.parse(data!.status)).toEqual(found());
  });

  it("sends quiet updates without an alert", () => {
    expect(androidPayload({ kind: "update" }, found(), 1300)).not.toHaveProperty("alert");
  });

  it("passes the linger time on end", () => {
    expect(androidPayload({ kind: "end", linger: 600 }, IDLE, 2000)).toMatchObject({ event: "end", linger: "600" });
    expect(androidPayload({ kind: "end" }, IDLE, 2000)?.linger).toBe("0");
  });

  it("skips the watch-only match alert, which the found update already covers", () => {
    expect(androidPayload({ kind: "matchAlert" }, found(), 1300)).toBeNull();
  });
});
