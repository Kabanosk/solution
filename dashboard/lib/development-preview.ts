// This flag cannot enable preview data or bypass login in production.
export function isDevelopmentPreview(): boolean {
  return process.env.NODE_ENV === "development" && process.env.DASHBOARD_PREVIEW === "1";
}
