/**
 * Gate adapters: turn each engine approval-gate proposal into editable table
 * rows (the Replit review screens) and turn edited rows back into the
 * `modified_data` shape AutonomousOrchestrator expects for that gate.
 */
import { humanize } from "./stages";
import type { GateName, PendingGate } from "./types";

export type Row = { id: string } & Record<string, string>;

export interface Column {
  key: string;
  header: string;
  width?: string;
  editable?: boolean;
  options?: string[]; // renders a select when editing
  multiline?: boolean;
}

export interface GateAdapter {
  /** Index in the progress stepper */
  step: number;
  title: string;
  description: string;
  columns: Column[];
  /** "edit": inline edits + add/delete rows; "remove": only include/exclude rows */
  mode: "edit" | "remove";
  canAddRows: boolean;
  newRow?: (index: number) => Row;
  toRows: (gate: PendingGate) => Row[];
  toModified: (rows: Row[], gate: PendingGate) => unknown;
}

const LEVELS = ["high", "medium", "low"];
const SEVERITIES = ["critical", "high", "medium", "low"];

const pad = (n: number) => String(n).padStart(2, "0");
const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const asRecord = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

// ── Stage 0: what discovery learned by talking to the bot (endpoint-only) ──

const markdownless = (text: string) => text.replace(/\*\*/g, "").trim();

const botDiscovery: GateAdapter = {
  step: 1,
  title: "What Your Bot Told Us",
  description:
    "AI SimTest chatted with your bot to learn what it does. Approve to build the test from this, or reject to have it ask again with follow-up questions.",
  mode: "edit",
  canAddRows: false,
  columns: [
    { key: "finding", header: "Finding", width: "80%" },
    { key: "confidence", header: "Confidence", width: "20%" },
  ],
  toRows: (gate) =>
    gate.items.map((item) => ({
      id: item.id,
      finding: markdownless(str(item.content)),
      confidence: str(item.confidence).charAt(0).toUpperCase() + str(item.confidence).slice(1),
    })),
  toModified: (_rows, gate) => gate.items.map((item) => item.content),
};

// ── Stage 1: bot context (single object → one row per field) ─────────────

const CONTEXT_FIELDS: { key: string; label: string; list?: boolean }[] = [
  { key: "bot_name", label: "Bot name" },
  { key: "domain", label: "Domain" },
  { key: "purpose", label: "Purpose" },
  { key: "target_audience", label: "Target audience" },
  { key: "capabilities", label: "Capabilities", list: true },
  { key: "limitations", label: "Limitations", list: true },
  { key: "topics", label: "Topics", list: true },
  { key: "key_entities", label: "Key entities", list: true },
  { key: "tone_and_style", label: "Tone & style" },
];

const botContext: GateAdapter = {
  step: 1,
  title: "Gathered Domain Context",
  description:
    "AI SimTest has reviewed the uploaded context and inferred the domain knowledge required for testing.",
  mode: "edit",
  canAddRows: false,
  columns: [
    { key: "area", header: "Area", width: "18%" },
    { key: "context", header: "Inferred Context", width: "67%", editable: true, multiline: true },
    { key: "confidence", header: "Confidence", width: "15%" },
  ],
  toRows: (gate) => {
    const raw = gate.raw_data;
    const confidence = str(raw.confidence || "medium");
    return CONTEXT_FIELDS.map((f) => {
      const value = raw[f.key];
      return {
        id: f.key,
        area: f.label,
        context: Array.isArray(value) ? value.map(str).join("; ") : str(value),
        confidence: confidence.charAt(0).toUpperCase() + confidence.slice(1),
      };
    });
  },
  toModified: (rows, gate) => {
    const next: Record<string, unknown> = { ...gate.raw_data };
    for (const row of rows) {
      const field = CONTEXT_FIELDS.find((f) => f.key === row.id);
      if (!field) continue;
      next[field.key] = field.list
        ? row.context
            .split(";")
            .map((s) => s.trim())
            .filter(Boolean)
        : row.context.trim();
    }
    return next;
  },
};

// ── Stage 2: success criteria ────────────────────────────────────────────

const successCriteria: GateAdapter = {
  step: 2,
  title: "Success Criteria",
  description:
    "These are the criteria AI SimTest will use to judge whether the chatbot responses are successful.",
  mode: "edit",
  canAddRows: true,
  columns: [
    { key: "code", header: "Criteria ID", width: "9%" },
    { key: "category", header: "Category", width: "13%", editable: true },
    { key: "criterion", header: "Success Criteria", width: "36%", editable: true, multiline: true },
    { key: "rationale", header: "Rationale / Measurement", width: "30%", editable: true, multiline: true },
    { key: "importance", header: "Priority", width: "12%", editable: true, options: LEVELS },
  ],
  newRow: (i) => ({ id: `new-${Date.now()}`, code: `C-${pad(i + 1)}`, category: "custom", criterion: "", rationale: "", importance: "high" }),
  toRows: (gate) =>
    gate.items.map((item, i) => {
      const c = asRecord(item.content);
      return {
        id: item.id,
        code: `C-${pad(i + 1)}`,
        category: str(c.category),
        criterion: str(c.criterion ?? item.content),
        rationale: item.explanation,
        importance: str(c.importance || "medium"),
      };
    }),
  toModified: (rows) =>
    rows
      .filter((r) => r.criterion.trim())
      .map((r) => ({
        criterion: r.criterion.trim(),
        category: r.category.trim() || "general",
        importance: r.importance,
        rationale: r.rationale.trim(),
      })),
};

// ── Stage 3: guardrail rules ─────────────────────────────────────────────

function splitGuardrailExplanation(explanation: string) {
  const marker = "Example violation:";
  const idx = explanation.indexOf(marker);
  if (idx === -1) return { rationale: explanation.trim(), violation: "" };
  return {
    rationale: explanation.slice(0, idx).replace(/\|\s*$/, "").trim(),
    violation: explanation.slice(idx + marker.length).trim(),
  };
}

const guardrailRules: GateAdapter = {
  step: 3,
  title: "Guardrails",
  description: "These are the safety and compliance boundaries the chatbot must not violate.",
  mode: "edit",
  canAddRows: true,
  columns: [
    { key: "code", header: "Guardrail ID", width: "9%" },
    { key: "category", header: "Risk Area", width: "13%", editable: true },
    { key: "rule", header: "Rule", width: "34%", editable: true, multiline: true },
    { key: "violation", header: "Violation Example", width: "22%" },
    { key: "detection_method", header: "Detection", width: "11%", editable: true },
    { key: "severity", header: "Severity", width: "11%", editable: true, options: SEVERITIES },
  ],
  newRow: (i) => ({
    id: `new-${Date.now()}`,
    code: `G-${pad(i + 1)}`,
    category: "custom",
    rule: "",
    violation: "",
    detection_method: "llm_judge",
    severity: "high",
  }),
  toRows: (gate) =>
    gate.items.map((item, i) => {
      const c = asRecord(item.content);
      return {
        id: item.id,
        code: `G-${pad(i + 1)}`,
        category: str(c.category),
        rule: str(c.rule ?? item.content),
        violation: splitGuardrailExplanation(item.explanation).violation,
        detection_method: str(c.detection_method),
        severity: str(c.severity || "high"),
      };
    }),
  toModified: (rows) =>
    rows
      .filter((r) => r.rule.trim())
      .map((r) => ({
        rule: r.rule.trim(),
        category: r.category.trim() || "general",
        severity: r.severity,
        detection_method: r.detection_method.trim(),
      })),
};

// ── Stage 4: test plan (topics are rows; strategy items pass through) ────

const isTopic = (content: unknown) => asRecord(content).type === "topic";

const testPlan: GateAdapter = {
  step: 4,
  title: "Test Plan",
  description:
    "AI SimTest has generated a structured test plan covering happy paths, edge cases, adversarial scenarios, and compliance checks.",
  mode: "edit",
  canAddRows: true,
  columns: [
    { key: "code", header: "Scenario ID", width: "10%" },
    { key: "name", header: "Scenario", width: "26%", editable: true, multiline: true },
    { key: "description", header: "Expected Coverage", width: "34%" },
    { key: "priority", header: "Priority", width: "10%", editable: true, options: LEVELS },
    { key: "risk_level", header: "Risk Level", width: "10%", editable: true, options: LEVELS },
    { key: "estimated_personas", header: "Personas", width: "10%", editable: true },
  ],
  newRow: (i) => ({
    id: `new-${Date.now()}`,
    code: `TP-${pad(i + 1)}`,
    name: "",
    description: "",
    priority: "medium",
    risk_level: "medium",
    estimated_personas: "3",
  }),
  toRows: (gate) =>
    gate.items
      .filter((item) => isTopic(item.content))
      .map((item, i) => {
        const c = asRecord(item.content);
        return {
          id: item.id,
          code: `TP-${pad(i + 1)}`,
          name: str(c.name),
          description: item.explanation,
          priority: str(c.priority || "medium"),
          risk_level: str(c.risk_level || "medium"),
          estimated_personas: str(c.estimated_personas ?? 3),
        };
      }),
  toModified: (rows, gate) => [
    ...rows
      .filter((r) => r.name.trim())
      .map((r) => ({
        type: "topic",
        name: r.name.trim(),
        priority: r.priority,
        risk_level: r.risk_level,
        estimated_personas: Math.max(1, parseInt(r.estimated_personas, 10) || 3),
      })),
    // Persona strategy + conversation config are not edited here — keep them as generated
    ...gate.items.filter((item) => !isTopic(item.content)).map((item) => item.content),
  ],
};

export function testPlanStrategy(gate: PendingGate) {
  const strategy = gate.items.map((i) => asRecord(i.content)).find((c) => c.type === "persona_strategy");
  if (!strategy) return null;
  return {
    standard: Number(strategy.standard_pct ?? 0),
    edgeCase: Number(strategy.edge_case_pct ?? 0),
    adversarial: Number(strategy.adversarial_pct ?? 0),
    focusAreas: Array.isArray(strategy.focus_areas) ? strategy.focus_areas.map(str) : [],
  };
}

// ── Stage 5: personas (remove-only) ──────────────────────────────────────

function parsePersonaText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const idx = line.indexOf(":");
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return out;
}

const personas: GateAdapter = {
  step: 5,
  title: "Personas",
  description:
    "AI SimTest has generated realistic users who will interact with the chatbot during simulation. Remove any you don't want before the run.",
  mode: "remove",
  canAddRows: false,
  columns: [
    { key: "code", header: "Persona ID", width: "9%" },
    { key: "persona", header: "Persona", width: "17%" },
    { key: "type", header: "Type", width: "11%" },
    { key: "goal", header: "Goal", width: "25%" },
    { key: "behavior", header: "Behavior Style", width: "18%" },
    { key: "risk", header: "Risk Focus", width: "20%" },
  ],
  toRows: (gate) =>
    gate.items.map((item, i) => {
      const p = parsePersonaText(str(item.content));
      return {
        id: String(i), // index is what the engine uses to remove personas
        code: `P-${pad(i + 1)}`,
        persona: p.name ?? `Persona ${i + 1}`,
        type: humanize(p.type ?? ""),
        goal: p.goals ?? "",
        behavior: [p.tone, p.tech_level && `${p.tech_level} tech`].filter(Boolean).join(" · "),
        risk: p.tactics ?? p.topics ?? "",
      };
    }),
  toModified: (keptRows, gate) => {
    const kept = new Set(keptRows.map((r) => Number(r.id)));
    return { removed_indices: gate.items.map((_, i) => i).filter((i) => !kept.has(i)) };
  },
};

export const GATE_ADAPTERS: Record<GateName, GateAdapter> = {
  bot_discovery: botDiscovery,
  bot_context: botContext,
  success_criteria: successCriteria,
  guardrail_rules: guardrailRules,
  test_plan: testPlan,
  personas,
};
