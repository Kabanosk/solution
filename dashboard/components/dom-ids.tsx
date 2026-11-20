"use client";

import { useEffect } from "react";

let nextId = 1;

export function DomIds() {
  useEffect(() => {
    const assignIds = () => {
      document.querySelectorAll<Element>(":not([id]), [id='']").forEach((element) => {
        let candidate: string;
        do { candidate = `scout-${element.localName}-${nextId++}`; }
        while (document.getElementById(candidate));
        element.id = candidate;
      });
    };
    assignIds();
    const observer = new MutationObserver(assignIds);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);
  return null;
}
