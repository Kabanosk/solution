export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs" && process.env.SCOUT_STRATEGY_SCHEDULER === "1") {
    const { tickStrategy } = await import("./lib/strategy-store");
    const runtime = globalThis as typeof globalThis & { strategyTimer?: ReturnType<typeof setInterval> };
    if (runtime.strategyTimer) return;
    let running = false;
    const tick = async () => {
      if (running) return;
      running = true;
      try { await tickStrategy(); } catch { console.error("Strategy scheduler unavailable; will retry in one minute."); }
      finally { running = false; }
    };
    runtime.strategyTimer = setInterval(() => { void tick(); }, 60000);
    runtime.strategyTimer.unref();
    void tick();
  }
}
