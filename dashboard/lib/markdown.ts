// A small Markdown subset for chat answers: headings, paragraphs, bullet / numbered lists, fenced code, bold,
// italic, inline code and http(s) links. It produces plain data; the React renderer never injects raw HTML.

export type Inline =
  | { type: "text"; text: string }
  | { type: "strong"; children: Inline[] }
  | { type: "em"; children: Inline[] }
  | { type: "code"; text: string }
  | { type: "link"; href: string; children: Inline[] };

export type Block =
  | { type: "heading"; level: 1 | 2 | 3; children: Inline[] }
  | { type: "paragraph"; children: Inline[] }
  | { type: "list"; ordered: boolean; start: number; items: Inline[][] }
  | { type: "code"; text: string };

const SAFE_HREF = /^https?:\/\//i;

export function parseInline(source: string): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => { if (text) { out.push({ type: "text", text }); text = ""; } };
  let i = 0;
  while (i < source.length) {
    const rest = source.slice(i);
    let match: RegExpExecArray | null;
    if ((match = /^`([^`\n]+)`/.exec(rest))) { flush(); out.push({ type: "code", text: match[1] }); i += match[0].length; continue; }
    if ((match = /^(\*\*|__)(?=\S)([\s\S]*?\S)\1/.exec(rest))) { flush(); out.push({ type: "strong", children: parseInline(match[2]) }); i += match[0].length; continue; }
    if ((match = /^\*(?=[^\s*])([^*\n]*?[^\s*])\*/.exec(rest)) || (match = /^_(?=[^\s_])([^_\n]*?[^\s_])_(?![A-Za-z0-9])/.exec(rest))) {
      const before = source[i - 1];
      if (rest[0] === "*" || !before || !/[A-Za-z0-9]/.test(before)) { flush(); out.push({ type: "em", children: parseInline(match[1]) }); i += match[0].length; continue; }
    }
    if ((match = /^\[([^\]\n]+)\]\(([^)\s]+)\)/.exec(rest)) && SAFE_HREF.test(match[2])) {
      flush(); out.push({ type: "link", href: match[2], children: parseInline(match[1]) }); i += match[0].length; continue;
    }
    text += source[i];
    i += 1;
  }
  flush();
  return out;
}

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ type: "paragraph", children: parseInline(paragraph.join("\n")) });
    paragraph = [];
  };
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const fence = /^\s*```/.exec(line);
    if (fence) {
      flushParagraph();
      const body: string[] = [];
      index++;
      while (index < lines.length && !/^\s*```/.test(lines[index])) body.push(lines[index++]);
      blocks.push({ type: "code", text: body.join("\n") });
      continue;
    }
    const heading = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) {
      flushParagraph();
      blocks.push({ type: "heading", level: Math.min(heading[1].length, 3) as 1 | 2 | 3, children: parseInline(heading[2]) });
      continue;
    }
    const bullet = /^\s{0,3}([-*+]|\d{1,9}[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      const ordered = /\d/.test(bullet[1]);
      const items: Inline[][] = [];
      const start = ordered ? Number.parseInt(bullet[1], 10) : 1;
      let current = bullet[2];
      for (index++; index < lines.length; index++) {
        const next = /^\s{0,3}([-*+]|\d{1,9}[.)])\s+(.*)$/.exec(lines[index]);
        if (next && /\d/.test(next[1]) === ordered) { items.push(parseInline(current)); current = next[2]; continue; }
        if (!next && /^\s{2,}\S/.test(lines[index])) { current += `\n${lines[index].trim()}`; continue; } // continuation
        break;
      }
      items.push(parseInline(current));
      index--;
      blocks.push({ type: "list", ordered, start, items });
      continue;
    }
    if (!line.trim()) { flushParagraph(); continue; }
    paragraph.push(line);
  }
  flushParagraph();
  return blocks;
}
