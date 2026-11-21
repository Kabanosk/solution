import { test } from "node:test";
import assert from "node:assert/strict";
import { parseInline, parseMarkdown } from "../lib/markdown.ts";

test("bold, italic, code and safe links", () => {
  assert.deepEqual(parseInline("Pay **€612.40** now"), [
    { type: "text", text: "Pay " }, { type: "strong", children: [{ type: "text", text: "€612.40" }] }, { type: "text", text: " now" },
  ]);
  assert.deepEqual(parseInline("*soon* and `SC-1`"), [
    { type: "em", children: [{ type: "text", text: "soon" }] }, { type: "text", text: " and " }, { type: "code", text: "SC-1" },
  ]);
  assert.deepEqual(parseInline("snake_case_name stays"), [{ type: "text", text: "snake_case_name stays" }]);
  assert.deepEqual(parseInline("2 * 3 * 4"), [{ type: "text", text: "2 * 3 * 4" }]);
  assert.equal(parseInline("[x](https://a.example)")[0].type, "link");
  assert.ok(parseInline("[x](javascript:alert(1))").every(node => node.type !== "link"));
});

test("dashboard help links are local navigation, not open redirects or scripts", () => {
  assert.deepEqual(parseInline("[Connections](/#connections)"), [
    { type: "link", href: "#connections", children: [{ type: "text", text: "Connections" }] },
  ]);
  assert.equal(parseInline("[Needs me](/#tasks?scope=inbox)")[0].type, "link");
  for (const href of ["//evil.example", "/\\\\evil.example", "javascript:alert", "data:text/html,hi"]) {
    assert.ok(parseInline(`[x](${href})`).every(node => node.type !== "link"));
  }
});

test("html stays text", () => {
  assert.deepEqual(parseMarkdown("<b>hi</b>"), [{ type: "paragraph", children: [{ type: "text", text: "<b>hi</b>" }] }]);
});

test("headings, lists, code fences and paragraphs", () => {
  const blocks = parseMarkdown("### Missing invoices\n\n1. **Asimex** €612\n2. Metro\n\n- a\n- b\n\n```\nSELECT 1\n```\nDone.");
  assert.deepEqual(blocks.map(block => block.type), ["heading", "list", "list", "code", "paragraph"]);
  assert.equal(blocks[0].type === "heading" && blocks[0].level, 3);
  assert.ok(blocks[1].type === "list" && blocks[1].ordered && blocks[1].items.length === 2);
  assert.equal(blocks[1].type === "list" && blocks[1].items[0][0].type, "strong");
  assert.ok(blocks[2].type === "list" && !blocks[2].ordered);
  assert.equal(blocks[3].type === "code" && blocks[3].text, "SELECT 1");
});
