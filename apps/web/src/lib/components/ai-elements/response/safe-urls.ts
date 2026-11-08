// Keeps only safe URL schemes in rendered model replies.
//
// The reply text is model output, and the model reads tool results, page text
// and operator input it does not control — so a `[click](javascript:...)` in a
// reply is one prompt injection away. svelte-exmarkdown's GFM path
// percent-encodes URLs but keeps whatever scheme it was given, and nothing
// downstream sanitises: a `javascript:` link was clickable and a `data:`
// image src was loaded. This rehype pass runs on the hast tree before it is
// rendered and unwraps links (to their plain text) and drops images whose URL
// is not on the allowlist.
//
// Raw HTML is not a hole today: the parser keeps it as `raw` nodes and the
// Renderer prints those as escaped text. That stops being true the moment
// someone adds rehype-raw, and this pass only looks at `a`/`img` — at that
// point use a real sanitiser (rehype-sanitize), not this.
import type { HastNode, Plugin } from "svelte-exmarkdown";
import { gfmPlugin } from "svelte-exmarkdown/gfm";

// mailto is the only non-web scheme with a use in an operator's reply. Images
// get no mailto and no data:/blob: — nothing the agent says legitimately
// carries an inline image; attachments render through their own component.
const LINK_SCHEMES: ReadonlySet<string> = new Set(["http", "https", "mailto"]);
const IMAGE_SCHEMES: ReadonlySet<string> = new Set(["http", "https"]);

// Same rule as hast-util-sanitize: a colon before any `/`, `?` or `#` is a
// scheme; otherwise the URL is relative (path, `?query`, `#anchor` — GFM
// footnote links are `#user-content-fn-1`). It is an allowlist on purpose:
// browsers ignore tabs, newlines and leading control characters in a scheme
// (`java\tscript:` runs), and a denylist would have to know that; here such a
// scheme just fails to equal "https".
export function isSafeUrl(url: unknown, schemes: ReadonlySet<string>): boolean {
  if (typeof url !== "string") return false;
  const colon = url.indexOf(":");
  if (colon === -1) return true;
  for (const stop of ["/", "?", "#"]) {
    const at = url.indexOf(stop);
    if (at !== -1 && at < colon) return true;
  }
  return schemes.has(url.slice(0, colon).toLowerCase());
}

function clean(node: HastNode): void {
  if (!("children" in node) || !Array.isArray(node.children)) return;
  node.children = node.children.flatMap((child): HastNode[] => {
    if (child.type !== "element") return [child];
    clean(child);
    const props = child.properties ?? {};
    // Unwrap rather than strip `href`: an <a> without one still looks like a
    // link in this console's styling, and the operator should see the text.
    if (child.tagName === "a" && !isSafeUrl(props.href, LINK_SCHEMES)) {
      return child.children;
    }
    if (child.tagName === "img" && !isSafeUrl(props.src, IMAGE_SCHEMES)) {
      const alt = typeof props.alt === "string" ? props.alt : "";
      return alt ? [{ type: "text", value: alt } as HastNode] : [];
    }
    return [child];
  }) as typeof node.children;
}

export function safeUrlsPlugin(): Plugin {
  return { rehypePlugin: () => (tree: HastNode) => clean(tree) };
}

// The exact list response.svelte renders with, so the tests parse replies
// through the same pipeline the operator sees — a test of the plugin alone
// would still pass if the component stopped using it.
export function responsePlugins(): Plugin[] {
  return [gfmPlugin(), safeUrlsPlugin()];
}
