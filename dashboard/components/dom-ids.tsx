"use client";

import { useEffect } from "react";

let nextId = 1;

function assign(element: Element) {
  if (element.id) return;
  let candidate: string;
  do { candidate = `scout-${element.localName}-${nextId++}`; }
  while (document.getElementById(candidate));
  element.id = candidate;
}

/** Gives every element a unique id (AGENTS.md). Scans the document once, then only the nodes React adds,
 *  batched per animation frame, instead of re-querying the whole DOM on every mutation (chat streaming, polling). */
export function DomIds() {
  useEffect(() => {
    const scan = (root: ParentNode) => root.querySelectorAll<Element>(":not([id]), [id='']").forEach(assign);
    scan(document);
    let pending: Element[] = [];
    let frame = 0;
    const flush = () => {
      frame = 0;
      const roots = pending; pending = [];
      for (const root of roots) if (root.isConnected) { assign(root); scan(root); }
    };
    const observer = new MutationObserver(records => {
      for (const record of records) record.addedNodes.forEach(node => { if (node.nodeType === Node.ELEMENT_NODE) pending.push(node as Element); });
      if (pending.length && !frame) frame = requestAnimationFrame(flush);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    return () => { observer.disconnect(); if (frame) cancelAnimationFrame(frame); };
  }, []);
  return null;
}
