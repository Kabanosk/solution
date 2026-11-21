"use client";

/** Scope both requests and browser-only caches to the rendered organization and user. */
export function scopeKey(key: string): string {
  if (typeof document === "undefined") return `server:${key}`;
  const scope = document.querySelector<HTMLElement>("[data-scout-scope]")?.dataset.scoutScope;
  return `${scope ?? "signed-out"}:${key}`;
}
export function tenantFetch(path: string, init: RequestInit = {}) {
  const slug = window.location.pathname.split("/")[1];
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug ?? "")) return Promise.reject(new Error("Organization required"));
  const headers = new Headers(init.headers);
  headers.set("X-Scout-Organization", slug);
  return fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" }).then(response => {
    if (response.status === 401) window.location.replace(window.location.pathname);
    return response;
  });
}
