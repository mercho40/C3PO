import { Context } from "runed";
import type { ReadableBoxedValues, WritableBoxedValues } from "svelte-toolbelt";
import type { CodeRootProps } from "./types";
import createDOMPurify from "dompurify";
import type { HighlighterCore } from "shiki";

type CodeOverflowStateProps = WritableBoxedValues<{
  collapsed: boolean;
}>;

// Bind DOMPurify only in the browser
const DOMPurify =
  typeof window !== "undefined" ? createDOMPurify(window) : null;

type CodeRootStateProps = ReadableBoxedValues<{
  code: string;
  lang: NonNullable<CodeRootProps["lang"]>;
  hideLines: boolean;
  highlight: CodeRootProps["highlight"];
}>;

class CodeOverflowState {
  constructor(readonly opts: CodeOverflowStateProps) {
    this.toggleCollapsed = this.toggleCollapsed.bind(this);
  }

  toggleCollapsed() {
    this.opts.collapsed.current = !this.opts.collapsed.current;
  }

  get collapsed() {
    return this.opts.collapsed.current;
  }
}

class CodeRootState {
  highlighter: HighlighterCore | null = $state(null);

  constructor(
    readonly opts: CodeRootStateProps,
    readonly overflow?: CodeOverflowState,
  ) {
    // Dynamic, so Shiki's engine and grammars land in their own chunk instead
    // of the /chat route bundle. Until it resolves, `highlighted` falls back
    // to escaped plain text — the code is readable the whole time, it just
    // gains colour a moment later.
    void import("./shiki").then(({ highlighter }) =>
      highlighter.then((hl) => (this.highlighter = hl)),
    );
  }

  highlight(code: string) {
    return this.highlighter?.codeToHtml(code, {
      lang: this.opts.lang.current,
      // Single theme, not a light/dark pair: the console is permanently
      // dark, and the pair would require loading a light theme that is
      // never rendered.
      theme: "github-dark-default",
      transformers: [
        {
          pre: (el) => {
            el.properties.style = "";

            if (!this.opts.hideLines.current) {
              el.properties.class += " line-numbers";
            }

            return el;
          },
          line: (node, line) => {
            if (within(line, this.opts.highlight.current)) {
              node.properties.class =
                node.properties.class + " line--highlighted";
            }

            return node;
          },
        },
      ],
    });
  }

  get code() {
    return this.opts.code.current;
  }

  // Use DOMPurify in the browser, raw HTML as a fallback during SSR
  highlighted = $derived.by(() => {
    // Shiki loads asynchronously (and never during SSR). Rendering nothing
    // until it arrives would blank out a tool call's parameters at exactly
    // the moment the operator wants to read them, so fall back to escaped
    // plain text and let colour arrive when it arrives.
    const html = this.highlight(this.code) ?? plainCodeHtml(this.code);

    if (DOMPurify) {
      return DOMPurify.sanitize(html);
    }

    return html;
  });
}

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/**
 * Shiki's own markup, minus the highlighting.
 *
 * `code` is typed as a string but genuinely isn't always one: a tool part whose
 * input hasn't streamed in yet renders `JSON.stringify(undefined)`, which is
 * `undefined`, not `"undefined"`.
 */
function plainCodeHtml(code: string) {
  if (typeof code !== "string") return "";
  const escaped = code.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
  return `<pre class="shiki" tabindex="0"><code>${escaped
    .split("\n")
    .map((line) => `<span class="line">${line}</span>`)
    .join("\n")}</code></pre>`;
}

function within(num: number, range: CodeRootProps["highlight"]) {
  if (!range) return false;

  let within = false;

  for (const r of range) {
    if (typeof r === "number") {
      if (num === r) {
        within = true;
        break;
      }
      continue;
    }

    if (r[0] <= num && num <= r[1]) {
      within = true;
      break;
    }
  }

  return within;
}

class CodeCopyButtonState {
  constructor(readonly root: CodeRootState) {}

  get code() {
    return this.root.opts.code.current;
  }
}

const overflowCtx = new Context<CodeOverflowState>("code-overflow-state");
const ctx = new Context<CodeRootState>("code-root-state");

export function useCodeOverflow(props: CodeOverflowStateProps) {
  return overflowCtx.set(new CodeOverflowState(props));
}

export function useCode(props: CodeRootStateProps) {
  return ctx.set(new CodeRootState(props, overflowCtx.getOr(undefined)));
}

export function useCodeCopyButton() {
  return new CodeCopyButtonState(ctx.get());
}
