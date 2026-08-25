/**
 * Validating skill arguments against the bridge's parameter schemas.
 *
 * These schemas are now plain JSON Schema, produced by pydantic on the bridge
 * and delivered over MCP. TypeBox cannot check them: `Value.Check` dispatches
 * on TypeBox's own `[Kind]` symbol, which a plain schema does not carry, and
 * throws `ValueCheckUnknownTypeError` rather than returning false. That is a
 * throw, not a rejection — so the previous code would have 500'd on every
 * dry-run instead of returning a 422.
 *
 * Ajv is the standard JSON Schema validator and handles them directly. It also
 * applies `default` values while validating (`useDefaults`), which matters
 * here: the bridge declares sensible defaults for things like `timeout_s` and
 * `stop_distance_m`, and a caller who omits them should inherit the tuned
 * values rather than have the request rejected.
 */

import Ajv, { type ValidateFunction } from "ajv";

const ajv = new Ajv({
  // Fill in declared defaults rather than failing on absent optionals.
  useDefaults: true,
  allErrors: true,
  // pydantic emits schemas with $defs/$ref and annotation keywords Ajv does not
  // know; unknown keywords are documentation, not validation instructions.
  strict: false,
});

const compiled = new WeakMap<object, ValidateFunction>();

function validatorFor(schema: unknown): ValidateFunction {
  const key = schema as object;
  let fn = compiled.get(key);
  if (!fn) {
    fn = ajv.compile(schema as object);
    compiled.set(key, fn);
  }
  return fn;
}

export interface ValidationIssue {
  path: string;
  message: string;
}

export interface ValidationResult {
  ok: boolean;
  /** The arguments with declared defaults applied. */
  value: Record<string, unknown>;
  issues: ValidationIssue[];
}

/**
 * Check `args` against a bridge parameter schema, applying defaults.
 *
 * Never throws for a bad schema or bad input — a malformed schema is reported
 * as a validation issue, because a 500 on the dry-run path would hide exactly
 * the mismatch the dry-run exists to reveal.
 */
export function validateArgs(
  schema: unknown,
  args: Record<string, unknown>,
): ValidationResult {
  const value = { ...args };
  let validate: ValidateFunction;
  try {
    validate = validatorFor(schema);
  } catch (err) {
    return {
      ok: false,
      value,
      issues: [
        {
          path: "/",
          message: `unusable parameter schema from bridge: ${
            err instanceof Error ? err.message : String(err)
          }`,
        },
      ],
    };
  }

  const ok = validate(value) as boolean;
  return {
    ok,
    value,
    issues: ok
      ? []
      : (validate.errors ?? []).map((e) => ({
          path: e.instancePath || "/",
          message: e.message ?? "invalid",
        })),
  };
}
