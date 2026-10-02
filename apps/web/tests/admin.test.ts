/**
 * /settings' user rules and /health's command-log reading.
 *
 * Pinned: an admin cannot lock themselves out (demote/ban/revoke self) and is
 * told why in visible text, roles other than admin read as "Operador" (null
 * included), unknown 4xx refusals keep their verbatim text while a 5xx never
 * echoes its body (it can carry SQL), Eden's network shape is not blamed on
 * the server, a log row recorded `ok` whose body says it only logged is not
 * shown as success, and empty filters are not sent.
 */

import { describe, expect, test } from "bun:test";
import {
  adminErrorText,
  isAdminRole,
  roleLabel,
  selfActionBlock,
  selfRowNote,
} from "../src/lib/admin/users";
import {
  auditErrorText,
  auditQuery,
  formatDuration,
  rowOutcome,
  sourceLabel,
  whoLabel,
  type AuditRow,
} from "../src/lib/admin/audit";

describe("roles", () => {
  test("only admin is admin; null and 'user' are operators", () => {
    expect(roleLabel("admin")).toBe("Admin");
    expect(roleLabel(null)).toBe("Operador");
    expect(roleLabel("user")).toBe("Operador");
    expect(isAdminRole("user,admin")).toBe(true);
  });
});

describe("selfActionBlock", () => {
  test("an admin cannot demote, ban or revoke themselves", () => {
    expect(selfActionBlock("u1", "u1", "demote")).not.toBeNull();
    expect(selfActionBlock("u1", "u1", "ban")).not.toBeNull();
    expect(selfActionBlock("u1", "u1", "revoke")).not.toBeNull();
  });

  test("acting on someone else is Better Auth's call, not ours", () => {
    expect(selfActionBlock("u1", "u2", "demote")).toBeNull();
    expect(selfActionBlock("u1", "u2", "ban")).toBeNull();
  });
});

describe("adminErrorText", () => {
  test("known codes are Spanish", () => {
    expect(
      adminErrorText({ code: "YOU_CANNOT_BAN_YOURSELF", status: 400 }).text,
    ).toContain("No podés");
  });

  test("unknown codes keep the server's words verbatim", () => {
    const e = adminErrorText({
      code: "SOMETHING_NEW",
      message: "Rate limited",
      status: 429,
    });
    expect(e.text).toContain("429");
    expect(e.verbatim).toBe("Rate limited");
  });

  test("a 401 is a session problem", () => {
    expect(adminErrorText({ status: 401 }).text).toContain("sesión");
  });

  test("a 5xx says the server failed, and never echoes its message", () => {
    const e = adminErrorText({
      message: 'relation "user" does not exist',
      status: 500,
    });
    expect(e.text).toContain("falló");
    expect(e.text).not.toContain("rechazó");
    expect(e.verbatim).toBeNull();
  });
});

describe("selfRowNote", () => {
  test("nothing blocked, nothing said", () => {
    expect(selfRowNote([])).toBeNull();
  });

  test("names every blocked action in one visible sentence", () => {
    const note = selfRowNote(["revoke", "demote", "ban"])!;
    expect(note).toContain(
      "quitarte el rol de admin, bloquearte ni cerrar tus propias sesiones",
    );
    expect(note).toContain("otro admin");
    expect(note).toContain("Cerrar sesión");
  });

  test("only what applies: no demote, no admin advice", () => {
    const note = selfRowNote(["ban"])!;
    expect(note).toContain("bloquearte");
    expect(note).not.toContain("admin");
  });
});

describe("auditErrorText", () => {
  test("Eden's network shape (an Error value) is the API, not the server failing", () => {
    const e = auditErrorText(503, new TypeError("fetch failed"));
    expect(e.kind).toBe("noApi");
    expect(e.text).not.toContain("HTTP");
  });

  test("the timeout reads as a timeout", () => {
    const e = auditErrorText(503, new DOMException("timeout", "TimeoutError"));
    expect(e.kind).toBe("noApi");
    expect(e.text).toContain("a tiempo");
  });

  test("a 5xx says the server failed and never shows its body", () => {
    const e = auditErrorText(500, {
      message: "SELECT * FROM tool_call_log WHERE …",
    });
    expect(e.kind).toBe("failed");
    expect(e.text).toContain("falló");
    expect(e.text).not.toContain("rechazó");
    expect(e.verbatim).toBeNull();
    expect(e.text).not.toContain("SELECT");
  });

  test("a 4xx is a refusal, with back's validator words kept verbatim", () => {
    const e = auditErrorText(422, {
      error: "invalid_query",
      message: "status must be one of ok, error",
    });
    expect(e.kind).toBe("rejected");
    expect(e.text).toContain("rechazó");
    expect(e.verbatim).toBe("status must be one of ok, error");
  });

  test("401 is the session; 403 is the role", () => {
    expect(auditErrorText(401, null).kind).toBe("expired");
    expect(auditErrorText(403, { error: "admin_required" }).kind).toBe(
      "forbidden",
    );
  });
});

function row(overrides: Partial<AuditRow> = {}): AuditRow {
  return {
    id: "r1",
    createdAt: "2026-10-01T14:05:00.000Z",
    skillName: "wave",
    params: {},
    result: { status: "completed" },
    status: "ok",
    error: null,
    durationMs: 812,
    chatId: null,
    source: "direct",
    user: { id: "u1", name: "Ada", email: "ada@example.com" },
    ...overrides,
  };
}

describe("rowOutcome", () => {
  test("a stub that only logged is not shown as success", () => {
    const o = rowOutcome(
      row({ result: { status: "ok", phase: "logged_only" } }),
    );
    expect(o.kind).toBe("warn");
  });

  test("an error row with no stored result still says why", () => {
    const o = rowOutcome(
      row({ status: "error", result: null, error: "bridge_unavailable" }),
    );
    expect(o.kind).toBe("error");
    expect(o.detail).toBe("bridge_unavailable");
  });

  test("a firmware refusal stored as the result names the hint", () => {
    const o = rowOutcome(
      row({
        status: "error",
        result: { status: "failed", error: "rpc_error_code_7401" },
        error: "rpc_error_code_7401",
      }),
    );
    expect(o.kind).toBe("error");
    expect(o.text).toContain("brazo");
  });

  test("ok with no stored result is 'sent', not 'completed'", () => {
    expect(rowOutcome(row({ result: null })).text).toBe("Enviado");
  });
});

describe("auditQuery", () => {
  test("empty filters are omitted", () => {
    expect(auditQuery({ skill: "  ", status: "", source: "" }, null)).toEqual({
      limit: "50",
    });
  });

  test("filters and cursor are sent", () => {
    expect(
      auditQuery({ skill: "wave", status: "error", source: "voice" }, "r9", 20),
    ).toEqual({
      limit: "20",
      skill: "wave",
      status: "error",
      source: "voice",
      before: "r9",
    });
  });
});

describe("labels", () => {
  test("legacy rows have no source, and are not called direct", () => {
    expect(sourceLabel(null)).toBe("Sin origen");
    expect(sourceLabel("direct")).toBe("Directo");
  });

  test("an unknown user is never given a name", () => {
    expect(whoLabel(null)).toBe("Desconocido");
    expect(whoLabel({ id: "u1", name: "", email: "x@y.z" })).toBe("x@y.z");
  });

  test("durations", () => {
    expect(formatDuration(812)).toBe("812 ms");
    expect(formatDuration(2400)).toBe("2.4 s");
    expect(formatDuration(null)).toBe("—");
  });
});
