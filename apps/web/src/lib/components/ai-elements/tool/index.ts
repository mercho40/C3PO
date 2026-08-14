// The registry ships `tool` without a barrel file, unlike every other
// ai-element; adding one keeps call sites consistent (`import * as Tool`).
import Tool from "./tool.svelte";
import ToolHeader from "./tool-header.svelte";
import ToolContent from "./tool-content.svelte";
import ToolInput from "./tool-input.svelte";
import ToolOutput from "./tool-output.svelte";

export * from "./tool-context.svelte.js";

export {
  Tool,
  ToolHeader,
  ToolContent,
  ToolInput,
  ToolOutput,
  //
  Tool as Root,
  ToolHeader as Header,
  ToolContent as Content,
  ToolInput as Input,
  ToolOutput as Output,
};
