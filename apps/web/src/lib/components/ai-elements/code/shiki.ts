// Follows the best practices established in https://shiki.matsu.io/guide/best-performance
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import { createHighlighterCore } from "shiki/core";

// Deliberately short. This highlighter exists to render tool-call arguments and
// results, which are JSON, plus the occasional shell or Python snippet from the
// bridge. The registry's default set also mapped `text` onto the `markdown`
// grammar — and that grammar statically embeds ~50 other languages, which is
// what dragged Emacs Lisp and C++ into the client bundle and pushed the build
// past its heap limit. Adding a language here is cheap; adding `markdown`,
// `html`, `vue`, `svelte` or `php` is not, because each embeds many others.
const bundledLanguages = {
  json: () => import("@shikijs/langs/json"),
  bash: () => import("@shikijs/langs/bash"),
  python: () => import("@shikijs/langs/python"),
  typescript: () => import("@shikijs/langs/typescript"),
  text: () => import("@shikijs/langs/json"),
};

/** The languages configured for the highlighter */
export type SupportedLanguage = keyof typeof bundledLanguages;

/** A preloaded highlighter instance. */
export const highlighter = createHighlighterCore({
  // One theme: the console has no light mode (see `routes/layout.css`).
  themes: [import("@shikijs/themes/github-dark-default")],
  langs: Object.values(bundledLanguages),
  engine: createJavaScriptRegexEngine(),
});
