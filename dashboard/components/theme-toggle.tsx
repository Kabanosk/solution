"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    const sync = (event: StorageEvent) => {
      if (event.key !== "lantern-theme" && event.key !== null) return;
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
    try { localStorage.setItem("lantern-theme", next ? "dark" : "light"); } catch { /* The theme still works when storage is unavailable. */ }
  }

  return <Button id="lantern-theme-toggle" type="button" variant="outline" size="icon"
    className="size-11 shrink-0" aria-label="Dark theme" aria-pressed={dark}
    title={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>
    {dark ? <Sun id="lantern-theme-sun" className="size-4" aria-hidden="true" /> : <Moon id="lantern-theme-moon" className="size-4" aria-hidden="true" />}
  </Button>;
}
