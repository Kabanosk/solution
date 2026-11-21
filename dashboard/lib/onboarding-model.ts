/** Versioned setup wire contract. Credentials are never part of this display model. */
export type SetupStepId = "bank" | "invoicing" | "ksef" | "email" | "slack" | "network";
export type SetupStepState = "not_started" | "needs_configuration" | "connecting" | "sandbox_connected" | "connected" | "failed" | "skipped";
export type SetupStatus = "not_started" | "pending" | "in_progress" | "dismissed" | "completed";
export type SetupStep = { id: SetupStepId; state: SetupStepState; provider: string | null; message: string | null; display: Record<string, string | boolean | null> };
export type NetworkAnswers = { required: boolean; provider: string; protocol: string; systems: string[]; administrator: string; company_only: boolean };
export type Setup = { version: 1; status: SetupStatus; revision: number; steps: SetupStep[]; network: NetworkAnswers | null };
export type SetupUpdate = { revision: number; action: "skip" | "resume" | "complete" | "step" | "network"; step?: SetupStepId; state?: "skipped" | "not_started"; network?: NetworkAnswers };
export type SetupHandoff = { setupUrl: string; markdown: string; manifest: Record<string, unknown>; prompt: string; claudeUrl: string | null };
