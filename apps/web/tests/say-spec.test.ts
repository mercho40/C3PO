/** The `say` form is built from the bridge's schema, not a hardcoded copy. */

import { describe, expect, test } from "bun:test";
import { readSaySpec, detailLang } from "../src/lib/components/voice/say";

describe("readSaySpec", () => {
  test("reads pydantic's schema for say", () => {
    const spec = readSaySpec({
      type: "object",
      properties: {
        text: { type: "string", minLength: 1, maxLength: 500 },
        language: {
          type: "string",
          enum: ["spanish", "english", "chinese"],
          default: "spanish",
        },
        wait_for_completion: { type: "boolean", default: false },
      },
      required: ["text"],
    });
    expect(spec).toEqual({
      maxLength: 500,
      minLength: 1,
      languages: ["spanish", "english", "chinese"],
      defaultLanguage: "spanish",
    });
  });

  test("follows a $ref into $defs", () => {
    const spec = readSaySpec({
      $defs: { Lang: { enum: ["spanish", "english"] } },
      properties: {
        text: { maxLength: 200 },
        language: { $ref: "#/$defs/Lang", default: "english" },
      },
    });
    expect(spec?.languages).toEqual(["spanish", "english"]);
    expect(spec?.maxLength).toBe(200);
    expect(spec?.defaultLanguage).toBe("english");
  });

  test("a schema without a text field is refused, not guessed", () => {
    expect(readSaySpec({ properties: { words: {} } })).toBeNull();
    expect(readSaySpec(null)).toBeNull();
  });
});

describe("detailLang", () => {
  test("bridge English is tagged en; the classifier's Spanish inherits es", () => {
    expect(detailLang("rpc_error_code_7401")).toBe("en");
    expect(detailLang("tts engine not ready")).toBe("en");
    expect(
      detailLang("La orden se envió; puede seguir ejecutándose en el robot."),
    ).toBeUndefined();
    expect(
      detailLang("¿Está activo c3po-bridge.service, o el túnel SSH?"),
    ).toBeUndefined();
  });
});
