import { activityPayload, IDLE, nextStatus, pushesFor, Status } from "../src/logic";

const queueing = (mode: Status["mode"] = "competitive"): Status => nextStatus(IDLE, { state: "queueing", mode, elapsed: 5 }, 1000);

describe("transitions", () => {
  it("starts the activity when a queue begins", () => {
    const next = queueing();
    expect(next).toEqual({ state: "queueing", mode: "competitive", startedAt: 995, foundAt: null });
    expect(pushesFor(IDLE, next)).toEqual([{ kind: "start", alert: "queue" }]);
  });

  it("sends nothing for a repeated report and keeps the start time", () => {
    const first = queueing();
    const again = nextStatus(first, { state: "queueing", mode: "competitive", elapsed: 0 }, 1100);
    expect(again.startedAt).toBe(995);
    expect(pushesFor(first, again)).toEqual([]);
  });

  it("alerts on match found and keeps the queue start", () => {
    const first = queueing();
    const found = nextStatus(first, { state: "found", mode: "competitive", elapsed: 300 }, 1300);
    expect(found).toEqual({ state: "found", mode: "competitive", startedAt: 995, foundAt: 1300 });
    expect(pushesFor(first, found)).toEqual([{ kind: "update", alert: "found" }, { kind: "matchAlert" }]);
  });

  it("ends the activity when the queue is left", () => {
    const first = queueing();
    expect(pushesFor(first, nextStatus(first, { state: "idle", mode: null, elapsed: 0 }, 1010))).toEqual([{ kind: "end" }]);
  });

  it("restarts after a found match goes back to queue", () => {
    const found = nextStatus(queueing(), { state: "found", mode: "competitive", elapsed: 0 }, 1300);
    const again = nextStatus(found, { state: "queueing", mode: "competitive", elapsed: 0 }, 1400);
    expect(again.startedAt).toBe(1400);
    expect(pushesFor(found, again)).toEqual([{ kind: "end" }, { kind: "start", alert: "queue" }]);
  });

  it("updates quietly on a mode change", () => {
    const first = queueing("quickPlay");
    const next = nextStatus(first, { state: "queueing", mode: "competitive", elapsed: 0 }, 1010);
    expect(pushesFor(first, next)).toEqual([{ kind: "update" }]);
  });
});

describe("payload", () => {
  it("builds a found alert with sound", () => {
    const found: Status = { state: "found", mode: "quickPlay", startedAt: 1000, foundAt: 1125 };
    const aps = activityPayload("update", found, 1125, "found");
    expect(aps.alert).toEqual({ title: "Match found!", body: "Quick Play · waited 2:05", sound: "match_found.caf" });
    expect(aps["interruption-level"]).toBe("time-sensitive");
    expect(aps["content-state"]).toEqual(found);
  });

  it("names the attributes type on start", () => {
    const aps = activityPayload("start", queueing(), 1000, "queue");
    expect(aps["attributes-type"]).toBe("QueueActivityAttributes");
    expect(aps.attributes).toEqual({});
  });
});
