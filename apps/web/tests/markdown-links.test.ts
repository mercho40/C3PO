/**
 * Model replies are markdown the model wrote after reading text it does not
 * control. Pinned here: a link or image in a reply can only point at a safe
 * scheme, and raw HTML in a reply never becomes an element.
 *
 * Replies are parsed through svelte-exmarkdown's own parser with the exact
 * plugin list response.svelte uses, so this fails if the component's
 * pipeline stops sanitising, not only if the helper does.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { HastNode, Parser, Plugin } from "svelte-exmarkdown";
import {
  isSafeUrl,
  responsePlugins,
} from "../src/lib/components/ai-elements/response/safe-urls";

// createParser is not in the package's exports map; load it from the file
// next to the resolved entry point (same dist folder, no version pinning).
const { createParser } = (await import(
  import.meta.resolve("svelte-exmarkdown").replace(/index\.js$/, "utils.js")
)) as { createParser: (plugins: Plugin[]) => Parser };

const parse = createParser(responsePlugins());

function elements(md: string, tagName: string): Record<string, unknown>[] {
  const found: Record<string, unknown>[] = [];
  const walk = (node: HastNode) => {
    if (node.type === "element" && node.tagName === tagName)
      found.push(node.properties ?? {});
    if ("children" in node && Array.isArray(node.children))
      node.children.forEach(walk);
  };
  walk(parse(md) as HastNode);
  return found;
}

function text(md: string): string {
  let out = "";
  const walk = (node: HastNode) => {
    if (node.type === "text") out += node.value;
    if ("children" in node && Array.isArray(node.children))
      node.children.forEach(walk);
  };
  walk(parse(md) as HastNode);
  return out;
}

// The checker's `bun:test` shim (tests/bun-test.d.ts) has no test.each, so
// the cases are plain loops.
const UNSAFE_LINKS: [string, string][] = [
  ["inline", "[click](javascript:alert(1))"],
  ["mixed case", "[click](JaVaScRiPt:alert(1))"],
  ["entity-encoded", "[click](&#106;avascript:alert(1))"],
  ["autolink", "<javascript:alert(1)>"],
  ["reference", "[r]: data:text/html,hi\n\n[click][r]"],
  ["vbscript", "[click](vbscript:msgbox(1))"],
  ["data", "[click](data:text/html;base64,PHNjcmlwdD4=)"],
];

const SAFE_LINKS: [string, string, string][] = [
  ["https", "[x](https://example.com/a?b=1#c)", "https://example.com/a?b=1#c"],
  [
    "http",
    "[x](http://192.168.123.164:8000/health)",
    "http://192.168.123.164:8000/health",
  ],
  ["mailto", "[x](mailto:ops@example.com)", "mailto:ops@example.com"],
  ["relative path", "[x](/health)", "/health"],
  ["anchor", "[x](#top)", "#top"],
  ["bare www (GFM)", "www.example.com", "http://www.example.com"],
];

const UNSAFE_IMAGES: [string, string][] = [
  ["javascript", "![pic](javascript:alert(1))"],
  ["data", "![pic](data:image/svg+xml;base64,PHN2Zz4=)"],
  ["mailto", "![pic](mailto:ops@example.com)"],
];

const RAW_HTML = [
  '<a href="javascript:alert(1)">x</a>',
  '<img src="x" onerror="alert(1)">',
  "<script>alert(1)</script>",
  '<iframe src="javascript:alert(1)"></iframe>',
];

describe("links in a reply", () => {
  for (const [kind, md] of UNSAFE_LINKS)
    test(`a ${kind} unsafe-scheme link is not a link`, () => {
      expect(elements(md, "a")).toEqual([]);
    });

  test("an unsafe link keeps its text, so the operator still reads it", () => {
    expect(text("see [the docs](javascript:alert(1)) now")).toBe(
      "see the docs now",
    );
  });

  for (const [kind, md, href] of SAFE_LINKS)
    test(`a ${kind} link survives`, () => {
      expect(elements(md, "a").map((p) => p.href)).toEqual([href]);
    });

  test("GFM footnote links survive", () => {
    const hrefs = elements("note[^1]\n\n[^1]: the source", "a").map(
      (p) => p.href,
    );
    expect(hrefs).toContain("#user-content-fn-1");
    expect(hrefs).toContain("#user-content-fnref-1");
  });
});

describe("images in a reply", () => {
  for (const [kind, md] of UNSAFE_IMAGES)
    test(`a ${kind} image src is not loaded, its alt text stays`, () => {
      expect(elements(md, "img")).toEqual([]);
      expect(text(md)).toBe("pic");
    });

  test("an https or relative image survives", () => {
    const src = (md: string) => elements(md, "img").map((p) => p.src);
    expect(src("![a](https://example.com/a.png)")).toEqual([
      "https://example.com/a.png",
    ]);
    expect(src("![a](/static/a.png)")).toEqual(["/static/a.png"]);
  });
});

describe("raw HTML in a reply", () => {
  for (const md of RAW_HTML)
    test(`${md} never becomes an element`, () => {
      for (const tag of ["a", "img", "script", "iframe"])
        expect(elements(md, tag)).toEqual([]);
    });
});

describe("isSafeUrl", () => {
  const web = new Set(["http", "https"]);

  test("tabs, newlines and leading control characters do not hide a scheme", () => {
    expect(isSafeUrl("java\tscript:alert(1)", web)).toBe(false);
    expect(isSafeUrl("java\nscript:alert(1)", web)).toBe(false);
    expect(isSafeUrl(" \u0001javascript:alert(1)", web)).toBe(false);
  });

  test("a colon after a path, query or fragment is not a scheme", () => {
    expect(isSafeUrl("/a:b", web)).toBe(true);
    expect(isSafeUrl("?t=12:30", web)).toBe(true);
    expect(isSafeUrl("#a:b", web)).toBe(true);
  });

  test("a missing URL is not safe", () => {
    expect(isSafeUrl(undefined, web)).toBe(false);
  });
});

describe("the chat bubble renders through the filtered pipeline", () => {
  test("response.svelte passes responsePlugins(), not a plugin list of its own", () => {
    // The tests above parse with responsePlugins(); this pins that the
    // component the operator sees uses the same list.
    const source = readFileSync(
      fileURLToPath(
        new URL(
          "../src/lib/components/ai-elements/response/response.svelte",
          import.meta.url,
        ),
      ),
      "utf8",
    );
    expect(source).toContain("plugins={responsePlugins()}");
    expect(source).not.toContain("gfmPlugin");
  });
});
