/**
 * Plain-language presets for the setup form. Each maps to engine settings;
 * "custom" hands the numbers back to the tester.
 */
import type { RunMode } from "@/lib/engine/types";

export type SizeId = "quick" | "standard" | "thorough" | "custom";
export interface SizePreset {
  id: SizeId;
  label: string;
  hint: string;
  personas: number;
  minTurns: number;
  maxTurns: number;
  parallel: number;
}

export const SIZES: SizePreset[] = [
  { id: "quick", label: "Quick check", hint: "5 customers, short chats — a few minutes", personas: 5, minTurns: 2, maxTurns: 6, parallel: 5 },
  { id: "standard", label: "Standard", hint: "20 customers, up to 10 messages each", personas: 20, minTurns: 3, maxTurns: 10, parallel: 5 },
  { id: "thorough", label: "Thorough", hint: "100 customers, up to 15 messages — release sign-off", personas: 100, minTurns: 3, maxTurns: 15, parallel: 10 },
  { id: "custom", label: "Custom", hint: "Choose the numbers yourself", personas: 20, minTurns: 3, maxTurns: 10, parallel: 5 },
];

export type StrictnessId = "lenient" | "standard" | "strict";
export interface StrictnessPreset {
  id: StrictnessId;
  label: string;
  hint: string;
  /** Quality judge pass mark and reply pass mark (0-1); null = engine default */
  quality: number | null;
  reply: number | null;
}

export const STRICTNESS: StrictnessPreset[] = [
  { id: "lenient", label: "Lenient", hint: "Short or partial answers pass when they are correct", quality: 0.45, reply: 0.6 },
  { id: "standard", label: "Standard", hint: "The engine's defaults (quality 0.60, reply 0.70)", quality: null, reply: null },
  { id: "strict", label: "Strict", hint: "Only complete, well-grounded answers pass", quality: 0.7, reply: 0.8 },
];

export interface ModeOption {
  id: RunMode;
  title: string;
  body: string;
  needs: string;
}

export const MODES: ModeOption[] = [
  {
    id: "partial",
    title: "From your documentation",
    body: "Upload what your bot answers from. AI drafts success criteria, guardrails, a test plan and personas.",
    needs: "Recommended",
  },
  {
    id: "auto",
    title: "From the bot itself",
    body: "No documents? AI chats with your bot to learn what it does, then builds the test from that.",
    needs: "Endpoint only",
  },
  {
    id: "manual",
    title: "From your own checklist",
    body: "Write the success criteria, rules and topics yourself. AI only drafts the bot's context and personas.",
    needs: "You decide what's tested",
  },
];

// Steps a guided run stops at for review, per mode
export const REVIEW_STEPS: Record<RunMode, number> = { partial: 5, auto: 6, manual: 5 };

/**
 * A rough cost range, from measured runs: about $0.026 per bot reply for the
 * quality judge and simulated user, $0.012 more with the guardrail LLM, $0.004
 * with LLM relevance, plus ~$0.05 per persona for setup and workflow checks.
 * Replies average ~80% of the midpoint between min and max turns.
 */
export function estimate(personas: number, minTurns: number, maxTurns: number, guardrailLlm: boolean, relevanceLlm: boolean) {
  const replies = Math.max(personas * minTurns, Math.round(personas * ((minTurns + maxTurns) / 2) * 0.8));
  const perReply = 0.026 + (guardrailLlm ? 0.012 : 0) + (relevanceLlm ? 0.004 : 0);
  const cost = replies * perReply + personas * 0.05 + 0.6;
  return { replies, low: cost * 0.7, high: cost * 1.3 };
}

export const money = (n: number) => (n < 10 ? `$${n.toFixed(2)}` : `$${Math.round(n)}`);
