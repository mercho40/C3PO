/**
 * The `say` skill's limits, read from the bridge's own JSON Schema.
 *
 * The bridge declares them (`mcp_server.py`: text 1..500 chars, language one of
 * spanish | english | chinese, default spanish) and the console reads them from
 * GET /skills/say instead of hardcoding a copy that would drift. Back validates
 * against the same schema on /invoke, so a form built from it cannot send what
 * back will refuse with a 422.
 *
 * Plain module, tested.
 */

export type SaySpec = {
  maxLength: number;
  minLength: number;
  languages: string[];
  defaultLanguage: string;
};

/** Spanish names for the bridge's language tokens; unknown tokens show as-is. */
export const LANGUAGE_LABEL: Record<string, string> = {
  spanish: "Español",
  english: "Inglés",
  chinese: "Chino",
};

type JsonSchema = {
  properties?: Record<string, JsonSchema>;
  $defs?: Record<string, JsonSchema>;
  $ref?: string;
  enum?: unknown[];
  const?: unknown;
  anyOf?: JsonSchema[];
  default?: unknown;
  maxLength?: number;
  minLength?: number;
};

function deref(
  root: JsonSchema,
  node: JsonSchema | undefined,
): JsonSchema | undefined {
  if (!node?.$ref) return node;
  const name = node.$ref.split("/").pop() ?? "";
  return root.$defs?.[name] ?? node;
}

/**
 * Read the limits, or null when the schema does not describe a `text` field —
 * a bridge whose `say` changed shape should disable the box, not be guessed at.
 */
export function readSaySpec(parameters: unknown): SaySpec | null {
  if (!parameters || typeof parameters !== "object") return null;
  const root = parameters as JsonSchema;
  const text = deref(root, root.properties?.text);
  if (!text) return null;
  const lang = deref(root, root.properties?.language);
  const fromEnum = (
    lang?.enum ??
    lang?.anyOf?.flatMap(
      (a) => a.enum ?? (a.const !== undefined ? [a.const] : []),
    ) ??
    []
  ).filter((v): v is string => typeof v === "string");
  const languages = fromEnum.length ? fromEnum : [];
  // pydantic puts `default` beside the `$ref`, not inside the referenced def.
  const declared = root.properties?.language?.default ?? lang?.default;
  const defaultLanguage =
    typeof declared === "string" ? declared : (languages[0] ?? "");
  return {
    maxLength: typeof text.maxLength === "number" ? text.maxLength : 500,
    minLength: typeof text.minLength === "number" ? text.minLength : 1,
    languages,
    defaultLanguage,
  };
}

/**
 * The `lang` for an outcome's `detail`. It is "the bridge's own explanation,
 * verbatim (often English)" — but the classifier writes some details itself,
 * in Spanish ("La orden se envió; puede seguir ejecutándose en el robot."), and
 * tagging those `en` makes a screen reader read Spanish with English phonetics.
 * No bridge string carries Spanish orthography; the classifier's nearly always
 * do. Undefined inherits the page's `es`.
 */
export function detailLang(text: string): "en" | undefined {
  return /[áéíóúñ¿¡]/i.test(text) ? undefined : "en";
}
