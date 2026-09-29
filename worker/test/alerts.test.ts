import { alertMessage } from "../src/fcm";
import { matchAlertOptions, SpeedTest, Status, testView } from "../src/logic";

type Apns = { headers: Record<string, string>; payload: { aps: Record<string, unknown> } & Record<string, unknown> };
const apns = (message: object): Apns => (message as { message: { apns: Apns } }).message.apns;

const found: Status = { state: "found", mode: "competitive", startedAt: 995, foundAt: 1300 };

describe("match alert", () => {
  it("is a plain time-sensitive alert when nothing else is asked for", () => {
    const { headers, payload } = apns(alertMessage("tok", "Match found!", "Competitive"));
    expect(headers).toEqual({ "apns-priority": "10", "apns-push-type": "alert" });
    expect(payload).toEqual({
      aps: { alert: { title: "Match found!", body: "Competitive" }, sound: "match_found.caf", "interruption-level": "time-sensitive" },
    });
  });

  it("gives the phone and the Watch the same ID, and drops the alert once the match is underway", () => {
    const options = matchAlertOptions(found);
    expect(options).toEqual({ collapseId: "found-1300", expiresAt: 1360 });
    const { headers } = apns(alertMessage("tok", "Match found!", "Competitive", options));
    expect(headers["apns-collapse-id"]).toBe("found-1300");
    expect(headers["apns-expiration"]).toBe("1360");
  });

  it("never expires an alert whose match has no found time", () => {
    const options = matchAlertOptions({ ...found, foundAt: null });
    expect(options).toEqual({ collapseId: "found" });
    expect(apns(alertMessage("tok", "Match found!", "Competitive", options)).headers).not.toHaveProperty("apns-expiration");
  });

  it("carries data next to aps", () => {
    const { payload } = apns(alertMessage("tok", "t", "b", { data: { status: found } }));
    expect(payload.status).toEqual(found);
  });

  it("can't have its aps overwritten by the data", () => {
    const { payload } = apns(alertMessage("tok", "t", "b", { data: { aps: { nope: 1 } } }));
    expect(payload.aps.alert).toEqual({ title: "t", body: "b" });
  });
});

describe("speed test", () => {
  const test = (devices: SpeedTest["devices"]): SpeedTest => ({ id: "a".repeat(32), startedAt: 10_000, devices });

  it("splits each device's time into reaching Firebase and reaching the device", () => {
    const view = testView(test({ phone: { sentAt: 10_300, arrivedAt: 11_200 } }), 12_000);
    expect(view).toEqual({
      testId: "a".repeat(32),
      elapsedMs: 2000,
      timeoutSeconds: 60,
      devices: { phone: { toServiceMs: 300, toDeviceMs: 900, error: null } },
    });
  });

  it("still adds up when the device answered before Firebase did", () => {
    const view = testView(test({ watch: { sentAt: 10_500, arrivedAt: 10_400 } }), 11_000);
    expect(view.devices).toEqual({ watch: { toServiceMs: 400, toDeviceMs: 0, error: null } });
  });

  it("shows a device that hasn't answered, and one that was rejected", () => {
    const view = testView(test({ phone: { sentAt: 10_200 }, android: { error: "UNREGISTERED" } }), 11_000);
    expect(view.devices).toEqual({
      phone: { toServiceMs: 200, toDeviceMs: null, error: null },
      android: { toServiceMs: null, toDeviceMs: null, error: "UNREGISTERED" },
    });
  });
});
