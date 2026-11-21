export function legalLinks(): { privacy: string; terms: string } {
  const published = (value: string | undefined) => {
    try { return value && new URL(value).protocol === "https:" ? value : undefined; }
    catch { return undefined; }
  };
  return { privacy: published(process.env.SCOUT_PRIVACY_URL) ?? "/privacy", terms: published(process.env.SCOUT_TERMS_URL) ?? "/terms" };
}
