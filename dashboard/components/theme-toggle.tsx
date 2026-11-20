"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ThemeToggle({ className }: { className?: string }) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    const sync = (event: StorageEvent) => {
      if (event.key !== "scout-theme" && event.key !== null) return;
      const next = event.newValue === "dark";
      document.documentElement.classList.toggle("dark", next);
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", next ? "#101010" : "#f7f7f7");
      setDark(next);
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", next ? "#101010" : "#f7f7f7");
    setDark(next);
    try { localStorage.setItem("scout-theme", next ? "dark" : "light"); } catch { /* The theme still works when storage is unavailable. */ }
  }

  return <Button id="scout-theme-toggle" type="button" variant="outline" size="icon"
    className={cn("size-11 shrink-0", className)} aria-label="Dark theme" aria-pressed={dark}
    title={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>
    {dark ? <Sun id="scout-theme-sun" className="size-4" aria-hidden="true" /> : <Moon id="scout-theme-moon" className="size-4" aria-hidden="true" />}
  </Button>;
}
