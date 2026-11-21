import { Fragment, useMemo } from "react";
import { parseMarkdown, type Inline } from "@/lib/markdown";

function Inlines({ nodes }: { nodes: Inline[] }) {
  return <>{nodes.map((node, index) => {
    switch (node.type) {
      case "text": return <Fragment key={index}>{node.text}</Fragment>;
      case "strong": return <strong key={index} className="font-semibold text-foreground"><Inlines nodes={node.children} /></strong>;
      case "em": return <em key={index}><Inlines nodes={node.children} /></em>;
      case "code": return <code key={index} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{node.text}</code>;
      case "link": return <a key={index} href={node.href} target={node.href.startsWith("#") ? undefined : "_blank"} rel={node.href.startsWith("#") ? undefined : "noreferrer noopener"} className="underline underline-offset-2 hover:text-foreground"><Inlines nodes={node.children} /></a>;
    }
  })}</>;
}

/** Chat answers as Markdown (subset). Everything is rendered as React text; raw HTML is never interpreted. */
export function Markdown({ source, className }: { source: string; className?: string }) {
  const blocks = useMemo(() => parseMarkdown(source), [source]);
  return <div className={className}>{blocks.map((block, index) => {
    switch (block.type) {
      case "heading": {
        const Tag = (`h${block.level + 2}`) as "h3" | "h4" | "h5";
        return <Tag key={index} className={block.level === 1 ? "mt-4 mb-1.5 text-base font-semibold first:mt-0" : "mt-4 mb-1 text-[15px] font-semibold first:mt-0"}><Inlines nodes={block.children} /></Tag>;
      }
      case "paragraph": return <p key={index} className="my-2 whitespace-pre-wrap first:mt-0 last:mb-0"><Inlines nodes={block.children} /></p>;
      case "list": {
        const items = block.items.map((item, itemIndex) => <li key={itemIndex} className="pl-1 whitespace-pre-wrap marker:text-muted-foreground"><Inlines nodes={item} /></li>);
        return block.ordered
          ? <ol key={index} start={block.start} className="my-2 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0">{items}</ol>
          : <ul key={index} className="my-2 list-disc space-y-1 pl-5 first:mt-0 last:mb-0">{items}</ul>;
      }
      case "code": return <pre key={index} className="my-2 overflow-x-auto rounded-lg border bg-muted/60 p-3 font-mono text-xs leading-5"><code>{block.text}</code></pre>;
    }
  })}</div>;
}
