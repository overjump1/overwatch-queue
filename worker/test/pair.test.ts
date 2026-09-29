import { beforeEach, vi } from "vitest";

// The Durable Object runs here on a Map instead of Cloudflare's storage, with every push recorded
// instead of sent.
vi.mock("cloudflare:workers", () => ({
  DurableObject: class {
    constructor(public ctx: unknown, public env: unknown) {}
  },
}));

const sent: object[] = [];
// What a send waits on before Firebase "answers", for holding one send up while others land.
let answer: (message: object) => Promise<void> = async () => undefined;
vi.mock("../src/fcm", async (original) => ({
  ...(await original<typeof import("../src/fcm")>()),
  parseServiceAccount: () => ({ project_id: "p", client_email: "e", private_key: "k" }),
  send: async (_account: unknown, message: object) => {
    sent.push(message);
    await answer(message);
    return { ok: true, status: 200 };
  },
}));

const { Pair } = await import("../src/index");

type Message = {
  message: {
    token: string;
    apns?: { headers: Record<string, string>; payload: { aps: Record<string, unknown> } & Record<string, unknown> };
    data?: Record<string, string>;
  };
};

function storage() {
  const map = new Map<string, unknown>();
  return {
    get: async (key: string) => structuredClone(map.get(key)),
    put: async (key: string, value: unknown) => void map.set(key, structuredClone(value)),
    delete: async (keys: string | string[]) => {
      for (const key of [keys].flat()) map.delete(key);
    },
    deleteAll: async () => map.clear(),
    setAlarm: async () => undefined,
    deleteAlarm: async () => undefined,
  };
}

const PAIR = "a".repeat(32);
const TOKEN = (name: string) => `${name}_${"x".repeat(30)}`;
const ACTIVITY = "ab".repeat(40);

let pair: InstanceType<typeof Pair>;
const messages = () => sent as Message[];
const to = (name: string) => messages().filter((m) => m.message.token === TOKEN(name));

beforeEach(async () => {
  sent.length = 0;
  answer = async () => undefined;
  pair = new Pair({ storage: storage() } as never, { FCM_SERVICE_ACCOUNT: "{}" } as never);
  await pair.register({ kind: "phone", fcm: TOKEN("phone"), updateToken: ACTIVITY, startToken: ACTIVITY });
  await pair.report({ state: "queueing", mode: "competitive", elapsed: 5 });
  // The started activity's own token, which the phone reports as soon as it has it.
  await pair.register({ kind: "phone", updateToken: ACTIVITY });
  sent.length = 0;
});

describe("match found with a Watch paired", () => {
  beforeEach(async () => {
    await pair.register({ kind: "watch", fcm: TOKEN("watch") });
    sent.length = 0;
    await pair.report({ state: "found", mode: "competitive", elapsed: 60, sinceFound: 0 });
  });

  it("sends the Watch and the phone the same alert, first", () => {
    const [first, second] = messages().map((m) => m.message);
    expect([first.token, second.token].sort()).toEqual([TOKEN("phone"), TOKEN("watch")]);
    for (const message of [first, second]) {
      expect(message.apns!.headers["apns-push-type"]).toBe("alert");
      expect(message.apns!.payload.aps.alert).toMatchObject({ title: "Match found!" });
    }
    expect(first.apns!.headers["apns-collapse-id"]).toMatch(/^found-\d+$/);
    expect(first.apns!.headers["apns-collapse-id"]).toBe(second.apns!.headers["apns-collapse-id"]);
  });

  it("gives the Watch the state with its alert, and no silent push after", () => {
    const watch = to("watch");
    expect(watch).toHaveLength(1);
    expect(watch[0].message.apns!.payload.status).toMatchObject({ state: "found" });
  });

  it("changes the Live Activity without a second alert", () => {
    const activity = to("phone").filter((m) => m.message.apns!.headers["apns-push-type"] === "liveactivity");
    expect(activity).toHaveLength(1);
    expect(activity[0].message.apns!.payload.aps).not.toHaveProperty("alert");
    expect(activity[0].message.apns!.payload.aps["content-state"]).toMatchObject({ state: "found" });
    expect(to("phone").filter((m) => m.message.apns!.headers["apns-push-type"] === "alert")).toHaveLength(1);
  });
});

describe("match found with no Watch", () => {
  it("leaves the alert to the Live Activity, as before", async () => {
    await pair.report({ state: "found", mode: "competitive", elapsed: 60, sinceFound: 0 });
    const phone = to("phone");
    expect(phone).toHaveLength(1);
    expect(phone[0].message.apns!.headers["apns-push-type"]).toBe("liveactivity");
    expect(phone[0].message.apns!.payload.aps.alert).toMatchObject({ title: "Match found!" });
  });
});

describe("speed test", () => {
  it("sends a test alert to every paired device and times each answer", async () => {
    await pair.register({ kind: "watch", fcm: TOKEN("watch") });
    await pair.register({ kind: "android", fcm: TOKEN("android") });
    sent.length = 0;
    const started = await pair.startTest(PAIR);
    expect(started.status).toBe(200);
    const { testId, devices } = started.body as { testId: string; devices: Record<string, unknown> };
    expect(Object.keys(devices).sort()).toEqual(["android", "phone", "watch"]);

    const [phone] = to("phone");
    expect(phone.message.apns!.payload).toMatchObject({ test: testId, pair: PAIR });
    expect(to("watch")[0].message.apns!.headers["apns-collapse-id"])
      .toBe(phone.message.apns!.headers["apns-collapse-id"]);
    expect(to("android")[0].message.data).toMatchObject({ event: "test", test: testId, pair: PAIR });

    expect((await pair.testArrived(testId, { kind: "android" })).status).toBe(200);
    const read = await pair.readTest(testId);
    const view = read.body as { devices: Record<string, { toDeviceMs: number | null }> };
    expect(view.devices.android.toDeviceMs).not.toBeNull();
    expect(view.devices.phone.toDeviceMs).toBeNull();
  });

  it("answers a device while another device's send is still out", async () => {
    await pair.register({ kind: "watch", fcm: TOKEN("watch") });
    await pair.register({ kind: "android", fcm: TOKEN("android") });
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    answer = async (message) => {
      if ((message as Message).message.token === TOKEN("watch")) await held;
    };
    const starting = pair.startTest(PAIR);
    await vi.waitFor(() => expect(to("android")).toHaveLength(1));
    const testId = to("android")[0].message.data!.test as string;

    // The Android phone's answer comes back while Firebase still hasn't answered for the Watch.
    expect((await pair.testArrived(testId, { kind: "android" })).status).toBe(200);
    release();
    const view = (await starting).body as { devices: Record<string, { toDeviceMs: number | null; error: string | null }> };
    expect(view.devices.android.toDeviceMs).not.toBeNull();
    expect(view.devices.watch.error).toBeNull();
  });

  it("takes one test at a time", async () => {
    expect((await pair.startTest(PAIR)).status).toBe(200);
    expect((await pair.startTest(PAIR)).status).toBe(429);
  });
});
