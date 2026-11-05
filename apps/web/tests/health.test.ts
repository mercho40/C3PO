/**
 * /health's classification. The properties pinned are the honesty ones: an
 * unknown subsystem never counts as healthy, a held reading never keeps its
 * colour, the corrupt-lowstate battery sample is called suspect, a gate we
 * cannot read is not "closed", a 503 ("nobody is publishing") is told apart
 * from a dead bridge, a failed poll prints no round trip, and known bridge
 * English reads in Spanish first while unknown text stays verbatim.
 */

import { describe, expect, test } from "bun:test";
import {
  classifyBattery,
  classifyCamera,
  classifyCatalogue,
  classifyFaults,
  classifyGate,
  classifyLink,
  classifyMap,
  classifyPerception,
  classifyServer,
  classifyVoice,
  overall,
  translateBridgeText,
  worstTone,
  type HealthRow,
  type LinkInput,
} from "../src/lib/robot/health";

const NOW = 1_000_000;

function row(tone: HealthRow["tone"]): HealthRow {
  return {
    id: tone,
    name: tone,
    tone,
    summary: "",
    age: null,
    detail: null,
    facts: [],
  };
}

const RAW = {
  tick: 1,
  mode_machine: 5,
  motor_count: 35,
  lowstate_messages_received: 1000,
  lowstate_age_s: 0.01,
  pose_source: "rt/odommodestate",
  pose_messages_received: 500,
  pose_age_s: 0.02,
  fsm_id: 801,
  fsm_mode: 0,
  fsm_age_s: 0.3,
  battery_soh_pct: 99,
  battery_current_ma: -1200,
  battery_messages_received: 50,
  battery_age_s: 0.5,
};

type RawPatch = Partial<Record<keyof typeof RAW, number | null>>;

function link(
  overrides: Partial<LinkInput> = {},
  raw: RawPatch = {},
): LinkInput {
  return {
    online: true,
    problem: null,
    latencyMs: 120,
    simulated: false,
    loaded: true,
    lastOkAt: NOW - 1000,
    state: {
      pose: { x_meters_world: 0, y_meters_world: 0, yaw_radians_world: 0 },
      battery_pct: 80,
      posture: "run",
      faults: [],
      env: "real",
      raw: { ...RAW, ...raw } as NonNullable<
        NonNullable<LinkInput["state"]>["raw"]
      >,
    },
    ...overrides,
  };
}

describe("overall", () => {
  test("never says all good while a row is unknown", () => {
    const o = overall([row("ok"), row("unknown")]);
    expect(o.tone).toBe("unknown");
    expect(o.summary).not.toContain("orden");
    expect(o.summary).toContain("sin datos");
  });

  test("worst tone wins: danger over warn over unknown over ok", () => {
    expect(worstTone(["ok", "unknown", "warn"])).toBe("warn");
    expect(worstTone(["warn", "danger", "unknown"])).toBe("danger");
    expect(overall([row("ok"), row("ok")]).summary).toContain("Todo en orden");
  });

  test("no rows is unknown, not fine", () => {
    expect(overall([]).tone).toBe("unknown");
  });
});

describe("classifyServer", () => {
  test("ok and fast", () => {
    const r = classifyServer(
      {
        state: "ok",
        at: NOW,
        ms: 40,
        status: 200,
        data: { status: "ok", timestamp: 1 },
      },
      NOW,
    );
    expect(r.tone).toBe("ok");
  });

  test("no answer at all is danger, worded as the API being unreachable", () => {
    const r = classifyServer(
      { state: "network", at: NOW, timeout: false },
      NOW,
    );
    expect(r.tone).toBe("danger");
    expect(r.summary).toBe("Sin acceso a la API");
  });

  test("not read yet is unknown", () => {
    expect(classifyServer({ state: "idle" }, NOW).tone).toBe("unknown");
  });
});

describe("classifyCatalogue", () => {
  test("a cached catalogue is a warning that names the bridge error verbatim", () => {
    const r = classifyCatalogue(
      {
        state: "ok",
        at: NOW,
        ms: 10,
        status: 200,
        data: {
          count: 38,
          source: "cache",
          age_seconds: 120,
          bridge_error: "ECONNREFUSED",
        },
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.bridgeText).toBe("ECONNREFUSED");
  });

  test("no bridge and nothing cached is danger", () => {
    const r = classifyCatalogue(
      {
        state: "ok",
        at: NOW,
        ms: 10,
        status: 200,
        data: {
          count: 0,
          source: "bridge",
          age_seconds: 0,
          bridge_error: "down",
        },
      },
      NOW,
    );
    expect(r.tone).toBe("danger");
  });

  test("rejected tools are surfaced, not silently dropped", () => {
    const r = classifyCatalogue(
      {
        state: "ok",
        at: NOW,
        ms: 10,
        status: 200,
        data: {
          count: 37,
          source: "bridge",
          age_seconds: 0,
          rejected: ["broken_tool"],
        },
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.facts.some((f) => f.value.includes("broken_tool"))).toBe(true);
  });

  test("502 bridge_unavailable says the bridge, in the shared vocabulary", () => {
    const r = classifyCatalogue(
      {
        state: "http",
        at: NOW,
        ms: 5,
        status: 502,
        body: { error: "bridge_unavailable" },
      },
      NOW,
    );
    expect(r.summary).toBe("Sin puente");
  });
});

describe("classifyLink", () => {
  test("healthy real link is ok", () => {
    expect(classifyLink(link(), NOW).tone).toBe("ok");
  });

  test("offline is danger and keeps the shared vocabulary", () => {
    const r = classifyLink(link({ online: false, problem: "noBridge" }), NOW);
    expect(r.tone).toBe("danger");
    expect(r.summary).toBe("Sin puente");
  });

  test("stub mode says the data is fictitious", () => {
    const r = classifyLink(link({ simulated: true }), NOW);
    expect(r.tone).toBe("warn");
    expect(r.summary).toContain("ficticios");
  });

  test("stale lowstate is a warning even while /state answers", () => {
    const r = classifyLink(link({}, { lowstate_age_s: 3.2 }), NOW);
    expect(r.tone).toBe("warn");
    expect(r.summary).toContain("rt/lowstate");
  });

  test("fsm_id null on real hardware with fresh lowstate names the post-reboot race and the restart", () => {
    const r = classifyLink(link({}, { fsm_id: null, fsm_age_s: null }), NOW);
    expect(r.tone).toBe("warn");
    expect(r.summary).toContain("fsm_id");
    expect(r.action ?? "").toContain("Reiniciá el servicio");
  });

  test("a failed poll prints no round trip — time-to-refused is not latency", () => {
    const r = classifyLink(
      link({ online: false, problem: "noApi", latencyMs: 5 }),
      NOW,
    );
    const fact = r.facts.find((f) => f.label === "Respuesta /state");
    expect(fact?.value).toBe("—");
    const ok = classifyLink(link({ latencyMs: 120 }), NOW).facts.find(
      (f) => f.label === "Respuesta /state",
    );
    expect(ok?.value).not.toBe("—");
  });

  test("fsm_id null in the simulator is normal (it is only polled on real)", () => {
    const l = link({}, { fsm_id: null, fsm_age_s: null });
    l.state!.env = "isaac";
    expect(classifyLink(l, NOW).tone).toBe("ok");
  });
});

describe("classifyBattery", () => {
  test("SoH above 100 marks the corrupt lowstate decode: suspect, not healthy", () => {
    const r = classifyBattery(link({}, { battery_soh_pct: 101 }), NOW);
    expect(r.tone).toBe("warn");
    expect(r.summary).toContain("sospechosa");
  });

  test("SoH 99 is real ageing and is NOT suspect", () => {
    expect(classifyBattery(link({}, { battery_soh_pct: 99 }), NOW).tone).toBe(
      "ok",
    );
  });

  test("current sign: negative is discharging, positive charging", () => {
    expect(
      classifyBattery(link({}, { battery_current_ma: -800 }), NOW).summary,
    ).toContain("descargando");
    expect(
      classifyBattery(link({}, { battery_current_ma: 900 }), NOW).summary,
    ).toContain("cargando");
  });

  test("offline keeps the last value but drops the colour", () => {
    const r = classifyBattery(link({ online: false, problem: "offline" }), NOW);
    expect(r.tone).toBe("unknown");
    expect(r.summary).toContain("Última lectura");
  });

  test("null battery is unknown, never full", () => {
    const l = link();
    l.state!.battery_pct = null;
    expect(classifyBattery(l, NOW).tone).toBe("unknown");
  });

  test("critical charge is danger", () => {
    const l = link();
    l.state!.battery_pct = 12;
    expect(classifyBattery(l, NOW).tone).toBe("danger");
  });
});

describe("classifyFaults", () => {
  test("no faults while online is ok; while stubbed it proves nothing", () => {
    expect(classifyFaults(link(), NOW).tone).toBe("ok");
    expect(classifyFaults(link({ simulated: true }), NOW).tone).toBe("unknown");
  });

  test("a danger fault makes the row danger, translated", () => {
    const l = link();
    l.state!.faults = ["low_battery_12pct", "stale_pose_30s"];
    const r = classifyFaults(l, NOW);
    expect(r.tone).toBe("danger");
    expect(r.faults?.[0]?.text).toContain("Batería baja");
  });
});

describe("classifyPerception", () => {
  test("a 503 is 'nobody is looking', not a dead bridge", () => {
    const r = classifyPerception(
      {
        state: "http",
        at: NOW,
        ms: 5,
        status: 503,
        body: { error: "could not build a snapshot" },
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.bridgeText).toBe("could not build a snapshot");
  });

  test("detector offline warns that an empty list is not a clear path", () => {
    const r = classifyPerception(
      {
        state: "ok",
        at: NOW,
        ms: 5,
        status: 200,
        data: {
          sources: { pose: "ok", detector: "offline" },
          report_age_s: 0.2,
        },
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.detail ?? "").toContain("camino libre");
  });

  test("every source offline is danger", () => {
    const r = classifyPerception(
      {
        state: "ok",
        at: NOW,
        ms: 5,
        status: 200,
        data: { sources: { pose: "offline", detector: "offline" } },
      },
      NOW,
    );
    expect(r.tone).toBe("danger");
  });
});

describe("classifyVoice", () => {
  const loop = { state: null, reason: null };
  const voice = (data: Record<string, unknown>) =>
    ({
      state: "ok",
      at: NOW,
      ms: 5,
      status: 200,
      data: {
        running: true,
        error: null,
        always_listening: false,
        mic_ever_open: false,
        seconds_since_audio: null,
        ...data,
      },
    }) as const;

  test("push-to-talk that nobody pressed is the resting state, not a failure", () => {
    expect(classifyVoice(voice({}), loop, NOW).tone).toBe("ok");
  });

  test("a stopped capture warns", () => {
    expect(classifyVoice(voice({ running: false }), loop, NOW).tone).toBe(
      "warn",
    );
  });

  test("an always-on mic silent for minutes warns", () => {
    expect(
      classifyVoice(
        voice({ always_listening: true, seconds_since_audio: 600 }),
        loop,
        NOW,
      ).tone,
    ).toBe("warn");
  });

  test("a mic error is danger, verbatim", () => {
    const r = classifyVoice(voice({ error: "ALSA: device busy" }), loop, NOW);
    expect(r.tone).toBe("danger");
    expect(r.bridgeText).toBe("ALSA: device busy");
  });
});

describe("classifyCamera", () => {
  const cfg = {
    robotUrl: "http://g1-orin.local:8001/camera",
    simHost: "",
    pageProtocol: "http:",
  };

  test("no robot URL is the simulator, unknown — not probed, never 'live'", () => {
    const r = classifyCamera(
      { state: "skipped", reason: "x" },
      { ...cfg, robotUrl: "" },
      NOW,
    );
    expect(r.tone).toBe("unknown");
    expect(r.summary).toBe("Modo simulador");
  });

  test("a network failure from an HTTPS console to an HTTP camera names mixed content", () => {
    const r = classifyCamera(
      { state: "network", at: NOW, timeout: false },
      { ...cfg, pageProtocol: "https:" },
      NOW,
    );
    expect(r.tone).toBe("danger");
    expect(r.detail ?? "").toContain("mixto");
  });

  test("not live with the bridge's hint", () => {
    const r = classifyCamera(
      {
        state: "ok",
        at: NOW,
        ms: 5,
        status: 200,
        data: {
          live: false,
          source: "none",
          frame_age_s: null,
          frames: 0,
          hint: "neither feed",
        },
      },
      cfg,
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.bridgeText).toBe("neither feed");
  });
});

describe("classifyMap", () => {
  test("503 is 'no Nav2 stage', with the bridge hint", () => {
    const r = classifyMap(
      {
        state: "http",
        at: NOW,
        ms: 5,
        status: 503,
        body: { error: "no costmap received", hint: "c3po perception up nav2" },
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.bridgeText).toBe("c3po perception up nav2");
  });

  test("the stale header is honoured", () => {
    const r = classifyMap(
      {
        state: "ok",
        at: NOW,
        ms: 5,
        status: 200,
        data: { bytes: 540 },
        headers: { "x-c3po-age-s": "42.00", "x-c3po-stale": "true" },
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
  });
});

describe("classifyGate", () => {
  const base = {
    enabled: false,
    disabled_reason: null,
    arm_expires_in_s: null,
    cmd_vel_received: 0,
    dropped_while_disabled: 0,
    clamped: 0,
    last_sent: null,
    last_sent_age_s: null,
    cmd_vel_age_s: null,
  };

  test("a gate we cannot read is unknown, never closed", () => {
    const r = classifyGate(
      {
        status: base,
        reason: "503",
        reasonFromBridge: true,
        loaded: true,
        lastOkAt: null,
      },
      NOW,
    );
    expect(r.tone).toBe("unknown");
    expect(r.summary).not.toContain("Cerrada");
  });

  test("armed shows the countdown", () => {
    const r = classifyGate(
      {
        status: { ...base, enabled: true, arm_expires_in_s: 23.4 },
        reason: null,
        loaded: true,
        lastOkAt: NOW,
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.summary).toContain("23 s");
  });

  test("closed is ok", () => {
    expect(
      classifyGate(
        { status: base, reason: null, loaded: true, lastOkAt: NOW },
        NOW,
      ).tone,
    ).toBe("ok");
  });

  test("'never armed' reads in Spanish first, the English kept verbatim", () => {
    const r = classifyGate(
      {
        status: { ...base, disabled_reason: "never armed" },
        reason: null,
        loaded: true,
        lastOkAt: NOW,
      },
      NOW,
    );
    expect(r.bridgeText).toBe("never armed");
    expect(r.bridgeEs).toContain("arrancó el puente");
  });

  test("the poller's own Spanish reason is detail, not English bridge text", () => {
    const r = classifyGate(
      {
        status: null,
        reason: "El servidor tardó demasiado en responder.",
        loaded: true,
        lastOkAt: null,
      },
      NOW,
    );
    expect(r.bridgeText ?? null).toBeNull();
    expect(r.detail ?? "").toContain("tardó demasiado");
  });

  test("an unreadable gate whose last reading was ARMED says so", () => {
    const r = classifyGate(
      {
        status: { ...base, enabled: true },
        reason: "unavailable",
        reasonFromBridge: true,
        loaded: true,
        lastOkAt: NOW - 9000,
      },
      NOW,
    );
    expect(r.tone).toBe("warn");
    expect(r.summary).toContain("ARMADA");
    expect(r.bridgeText).toBe("unavailable");
  });
});

describe("translateBridgeText", () => {
  test("known exact strings, case- and space-insensitive", () => {
    expect(translateBridgeText("never armed")).toContain("arrancó");
    expect(translateBridgeText("  Arm TTL expired ")).toContain("venció");
    expect(translateBridgeText("could not build a snapshot")).toContain(
      "escena",
    );
  });

  test("long hints match by prefix, so a reworded tail still translates", () => {
    expect(
      translateBridgeText(
        "the costmap comes from Nav2's global_costmap, so it exists only while…",
      ),
    ).toContain("nav2");
    expect(
      translateBridgeText(
        "frames stopped — most likely videohub_pc4 was killed",
      ),
    ).toContain("videohub_pc4");
    expect(
      translateBridgeText("neither camera feed is live. The vendor feed…"),
    ).toContain("Ninguna de las dos");
  });

  test("transport errors wrapped in other words still match", () => {
    expect(
      translateBridgeText("connect ECONNREFUSED 127.0.0.1:8001"),
    ).toContain("rechazó");
  });

  test("unknown text is never guessed at", () => {
    expect(translateBridgeText("ALSA: device busy")).toBeNull();
    expect(translateBridgeText("")).toBeNull();
    expect(translateBridgeText(null)).toBeNull();
  });

  test("rows carry both: Spanish when known, verbatim always", () => {
    const map = classifyMap(
      {
        state: "http",
        at: NOW,
        ms: 5,
        status: 503,
        body: {
          error: "no costmap received",
          hint: "the costmap comes from Nav2's global_costmap …",
        },
      },
      NOW,
    );
    expect(map.bridgeText).toContain("global_costmap");
    expect(map.bridgeEs).toContain("global_costmap de Nav2");
    const voice = classifyVoice(
      {
        state: "ok",
        at: NOW,
        ms: 5,
        status: 200,
        data: {
          running: true,
          error: "ALSA: device busy",
          always_listening: false,
          mic_ever_open: false,
          seconds_since_audio: null,
        },
      },
      { state: null, reason: null },
      NOW,
    );
    expect(voice.bridgeText).toBe("ALSA: device busy");
    expect(voice.bridgeEs ?? null).toBeNull();
  });
});
