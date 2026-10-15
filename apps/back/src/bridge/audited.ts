/**
 * `callTool`, with an audit row written when the call settles.
 *
 * For the paths that command the robot outside `/skills/:name/invoke` and the
 * agent — task cancel and its estop, the legacy voice loop's spoken "stop",
 * and the spoken replies — which used to call the bridge raw and leave no
 * row. "Every robot command is audited" was true of the routes that were
 * written to be, and not of these.
 *
 * Same rules as `/skills`: the row is never awaited (a database write must not
 * sit between anybody and a stop), `logToolCall` never throws, and the error
 * text keeps the bridge's own reason.
 */

import { logToolCall } from "#back/db/chats.js";
import type { ToolCallSource } from "#back/db/schema.js";

import { BridgeToolError, BridgeUnavailableError, callTool } from "./client";

export type AuditWho = { userId: string | null; source: ToolCallSource };

export async function auditedCall(
  name: string,
  args: Record<string, unknown>,
  who: AuditWho,
): Promise<unknown> {
  const startedAt = Date.now();
  try {
    const result = await callTool(name, args);
    void logToolCall({
      ...who,
      skillName: name,
      params: args,
      result,
      status: "ok",
      durationMs: Date.now() - startedAt,
    });
    return result;
  } catch (err) {
    const error =
      err instanceof BridgeToolError
        ? `tool_error: ${err.detail}`
        : err instanceof BridgeUnavailableError
          ? "bridge_unavailable"
          : "bridge_error";
    void logToolCall({
      ...who,
      skillName: name,
      params: args,
      result: { error },
      status: "error",
      error,
      durationMs: Date.now() - startedAt,
    });
    throw err;
  }
}
