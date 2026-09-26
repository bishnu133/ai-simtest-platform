/**
 * The kinds of test the platform offers. Each maps to an engine capability;
 * those not yet wired into the platform say how to run them from the CLI today.
 */
import {
  BookOpenCheck,
  BrainCircuit,
  Database,
  FileClock,
  GitCompareArrows,
  Layers,
  ListRestart,
  Users,
  type LucideIcon,
} from "lucide-react";

export type TestTypeId =
  | "simulation"
  | "scenarios"
  | "stress"
  | "replay"
  | "compare"
  | "regression"
  | "rag"
  | "calibration";

export interface TestType {
  id: TestTypeId;
  name: string;
  icon: LucideIcon;
  tagline: string;
  description: string;
  checks: string[];
  needs: string[];
  available: boolean;
  /** Where it lands in the Phase 2 plan, for types not yet in the platform */
  roadmap?: string;
  /** How to run it from the engine CLI today */
  cli?: string;
}

export const TEST_TYPES: TestType[] = [
  {
    id: "simulation",
    name: "Persona simulation",
    icon: Users,
    tagline: "Realistic customers talk to your bot; every reply is judged.",
    description:
      "AI reads your documentation, proposes success criteria, guardrails, a test plan and personas for you to review, then runs the conversations and judges every reply for quality, grounding, safety and your rules.",
    checks: ["Answer quality and relevance", "Grounding in your documentation", "Safety and approved guardrails", "Business workflows and compliance policy", "Conversation loops and stuck customers"],
    needs: ["Bot endpoint", "Your bot's documentation (Markdown)"],
    available: true,
  },
  {
    id: "scenarios",
    name: "Scenario packs",
    icon: Layers,
    tagline: "Structured situations: escalation, ambiguity, off-topic, prompt injection.",
    description:
      "Simulated customers take turns running curated scenarios, so specific behaviours are exercised on every run instead of left to chance, and each scenario gets its own pass rate.",
    checks: ["Emotional escalation and corrections", "Unclear, multi-part and changing requests", "Off-topic questions and prompt injection", "Remembering earlier details"],
    needs: ["Bot endpoint", "Documentation"],
    available: true,
  },
  {
    id: "stress",
    name: "Memory & context stress",
    icon: BrainCircuit,
    tagline: "Long conversations that test what the bot remembers.",
    description:
      "Long conversations in which the customer shares details early, changes their story and asks harder questions, then checks from the transcript whether the bot still carries the context.",
    checks: ["Recall of details across many messages", "Noticing contradictions", "Coping with growing complexity"],
    needs: ["Bot endpoint", "Documentation"],
    available: true,
  },
  {
    id: "replay",
    name: "Production replay",
    icon: FileClock,
    tagline: "Judge real conversations from production, with personal data masked.",
    description:
      "Upload chat logs; personal data is masked before anything reads them. Judge the logged replies, or send the customers' messages to your bot again to see how today's build handles real traffic.",
    checks: ["Real-world answer quality", "Safety and rule breaches in live traffic", "Personal data found and masked"],
    needs: ["Conversation logs (JSON Lines, JSON, CSV or text)", "Your bot's documentation"],
    available: true,
  },
  {
    id: "compare",
    name: "Model comparison",
    icon: GitCompareArrows,
    tagline: "Same personas against several models or configurations.",
    description:
      "Runs one persona set against two or more endpoints, models or keys and compares them judge by judge, including where one passed and another failed.",
    checks: ["Side-by-side pass rates", "Per-judge differences", "Conversations that diverge"],
    needs: ["Two or more bot endpoints or keys"],
    available: false,
    roadmap: "Phase 2 · Step 5",
    cli: "simtest multi-compare --config compare.yaml",
  },
  {
    id: "regression",
    name: "Regression suite",
    icon: ListRestart,
    tagline: "Save today's failures; re-run them against every new build.",
    description:
      "Turns a run's failures into a suite and replays it against a new build, reporting what is fixed, what still fails and what regressed.",
    checks: ["Fixed since last build", "Still failing", "New regressions"],
    needs: ["A finished run", "The bot endpoint to test"],
    available: false,
    roadmap: "Phase 2 · Step 6",
    cli: "simtest save-suite --report report.json --name banking-v1\nsimtest replay --suite suites/banking-v1 --bot-endpoint URL",
  },
  {
    id: "rag",
    name: "RAG & tool evaluation",
    icon: Database,
    tagline: "Retrieval accuracy, citations and tool calls.",
    description:
      "Checks whether the bot retrieved the right context, cited it, and called tools with the right parameters.",
    checks: ["Retrieval accuracy", "Citation quality", "Tool-call correctness"],
    needs: ["Bot endpoint", "Tool definitions (optional)"],
    available: false,
    roadmap: "Phase 2 · Step 8",
    cli: "simtest run --bot-endpoint URL --doc-file kb.md --rag-eval --tool-defs tools.json",
  },
  {
    id: "calibration",
    name: "Judge calibration",
    icon: BookOpenCheck,
    tagline: "Check the judges against human-labelled examples.",
    description:
      "Runs the judges on a golden set built from your Judge review labels and reports how often each agrees with a person.",
    checks: ["Agreement with human labels", "Judges too strict or too lenient", "Drift across rubric versions"],
    needs: ["A golden set (exported from Judge review)"],
    available: false,
    roadmap: "Phase 2 · Step 7",
    cli: "simtest calibrate --golden-file golden_set.json",
  },
];

export const testType = (id: string | null | undefined): TestType =>
  TEST_TYPES.find((t) => t.id === id) ?? TEST_TYPES[0];
