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
    tagline: "The same customers against several bots, models or prompts.",
    description:
      "Approve one set of customers, then every bot meets the same ones and is judged the same way. Results pair up customer by customer, say whether each difference is real or too close to call, and show the conversations where the bots went different ways side by side.",
    checks: ["Pass rates with a range, not just a number", "Per-judge differences", "Critical failures, stuck chats and speed", "Conversations that diverge, side by side"],
    needs: ["Two to four bot endpoints (or one endpoint with different model names or keys)", "Your bot's documentation"],
    available: true,
  },
  {
    id: "regression",
    name: "Regression suite",
    icon: ListRestart,
    tagline: "Replay saved failures on a new build: what's fixed, what broke.",
    description:
      "Save a finished run's failing conversations as a suite (Export on its report). Replaying sends the same customer messages to any build and judges the replies exactly as the original run was judged, then shows reply by reply what was fixed, what still fails and what regressed.",
    checks: ["Fixed since the suite was saved", "Still failing", "Replies that passed and now fail", "History of every replay"],
    needs: ["A saved suite", "The bot endpoint to test"],
    available: true,
  },
  {
    id: "rag",
    name: "RAG & tool evaluation",
    icon: Database,
    tagline: "What the bot retrieved, what it cited, and the tools it called.",
    description:
      "Simulated customers talk to your bot while every reply is checked against the sources it retrieved and the tools it called: figures that are not in the sources, citations their source does not support, tool results that never reach the reply, errors left unexplained, and actions taken without a check.",
    checks: ["Figures and claims backed by the retrieved sources", "Citations their source supports", "Tool results used, errors handled, retries", "Permissions and confirmations before changes"],
    needs: ["Bot endpoint", "Documentation", "Sources or tool calls in the bot's JSON response (optional but recommended)"],
    available: true,
    cli: "simtest run --bot-endpoint URL --doc-file kb.md --rag-eval --tool-defs tools.json",
  },
  {
    id: "calibration",
    name: "Judge calibration",
    icon: BookOpenCheck,
    tagline: "Check the judges against people's labels.",
    description:
      "Pools every reply you labelled in Judge review, across all runs, and shows how often each judge agrees with a person, which way it leans, and the pass mark that would agree most. A re-check puts the same replies to today's judges and lists every verdict that changed.",
    checks: ["Agreement with human labels", "Judges too strict or too lenient", "Drift across judge versions"],
    needs: ["Replies labelled in Judge review (any finished run)"],
    available: true,
    cli: "simtest calibrate --golden-file golden_set.json",
  },
];

export const testType = (id: string | null | undefined): TestType =>
  TEST_TYPES.find((t) => t.id === id) ?? TEST_TYPES[0];
