import type { BoardMessages, BoardTones, Level } from "./stages";
import type { SurfaceTone, Tone } from "./tokens";

/**
 * Seeded demo fixtures. These mirror the four scenarios the gateway already
 * serves (`scene1`, `clean`, `held`, `blocked`) so the demo path never needs
 * the network — a live demo that depends on a backend at Q&A time is a
 * liability. Live mode replaces these values from the SSE contract.
 */

export type SceneId = "scene1" | "clean" | "held" | "blocked";

/** Terminal node verdicts. `info` is neutral-but-resolved, never "checking". */
export type NodeState = Extract<Tone, "pass" | "warn" | "fail" | "info" | "idle">;

export interface NodeFixture {
  label: string;
  v: string;
  st: NodeState;
  /** null when the backend does not report a latency for this check. */
  ms: number | null;
  meta: string;
}

export interface SummaryFixture {
  v: number;
  prefix?: string;
  suffix?: string;
  dec?: number;
  label: string;
  note: string;
}

export interface Scene {
  label: string;
  outcome: string;
  tone: SurfaceTone;
  prompt: string;
  stakes: {
    impact: number;
    rev: "reversible" | "costly" | "irreversible";
    domain: string;
    model: string;
  };
  laneA: NodeFixture[];
  gen: string;
  laneB: NodeFixture[];
  costs: Record<Level, number>;
  /** Set in live mode when the loss table marks an action unavailable. */
  costMeta?: Partial<Record<Level, { available: boolean; reason: string | null }>>;
  chosen: Level;
  why: string;
  gate: { committed: string; buffered: string; title: string; tone: SurfaceTone; verdict: string };
  final: string;
  stamp: string;
  stampTone: SurfaceTone;
  counterfactual: string;
  summary: SummaryFixture[];
  board: BoardMessages;
  boardTone: BoardTones;
}

export const SCENES: Record<SceneId, Scene> = {
  scene1: {
    label: "Invented loan clause",
    outcome: "L2 repair",
    tone: "warn",
    prompt: "What are the prepayment charges on my floating-rate home loan?",
    stakes: { impact: 9000, rev: "costly", domain: "lending", model: "gpt-oss-20b" },
    laneA: [
      { label: "Injection check", v: "CLEAR", st: "pass", ms: 4.1, meta: "pattern set + classifier on the user turn" },
      { label: "PII check", v: "NONE", st: "pass", ms: 2.8, meta: "no account or card number in the prompt" },
      { label: "Canary check", v: "CLEAR", st: "pass", ms: 1.9, meta: "no seeded internal token present" },
      { label: "Stakes estimate", v: "₹9,000", st: "info", ms: 5.4, meta: "costly, reversible with rework · lending" },
      { label: "Cache lookup", v: "MISS", st: "warn", ms: 3.4, meta: "no semantically equivalent recent answer" },
      { label: "Route decision", v: "LARGE", st: "info", ms: 6.2, meta: "stakes above small-model threshold" },
    ],
    gen: "Prepayment on a floating-rate home loan carries no charge, and RBI circular 42/2019 fixes the maximum at 0.5% of the outstanding principal.",
    laneB: [
      { label: "Observer probe", v: "0.71", st: "fail", ms: 210, meta: "semantic-entropy probe, generator-agnostic" },
      { label: "Claim verifier", v: "1 OF 3 UNSUPPORTED", st: "fail", ms: 288, meta: "circular number and 0.5% cap absent from d004, d011" },
      { label: "Forecast", v: "RISING", st: "warn", ms: 96, meta: "next sentence likely to extend the invented citation" },
    ],
    costs: { L0: 412, L1: 96, L2: 31, L3: 188, L4: 640, L5: 900 },
    chosen: "L2",
    why: "why: grounding.citation_unsupported 0.94 · one sentence isolated · repair cost below annotate at this impact",
    gate: {
      committed: "Prepayment on a floating-rate home loan carries no charge for individual borrowers.",
      buffered: "RBI circular 42/2019 fixes the maximum at 0.5% of the outstanding principal.",
      title: "buffered · never released",
      tone: "warn",
      verdict: "Sentence 2 was repaired in 214 ms. The invented circular never reached the customer.",
    },
    final:
      "Prepayment on a floating-rate home loan carries no charge for individual borrowers. I could not verify a specific RBI circular for a percentage cap, so I have not quoted one — the branch can confirm your exact schedule.",
    stamp: "L2 REPAIR",
    stampTone: "warn",
    counterfactual:
      "Prepayment on a floating-rate home loan carries no charge, and RBI circular 42/2019 fixes the maximum at 0.5% of the outstanding principal.",
    summary: [
      { v: 147, suffix: " ms", prefix: "+", label: "added latency", note: "decision path, excludes generation" },
      { v: 6.2, suffix: "%", dec: 1, label: "verification cost", note: "of this request’s model spend" },
      { v: 1412, prefix: "−₹", label: "net vs no oversight", note: "rework avoided minus checking" },
      { v: 1, suffix: " / 1", label: "defects caught pre-action", note: "invented citation, caught mid-stream" },
    ],
    board: {
      laneA: "LANE A CLEAR\n25 MS",
      gen: "MODEL TALKING\nGPT-OSS-20B",
      laneB: "PROBE 0.71\nCLAIM UNSUPPORTED",
      ladder: "SIX PRICED\nL2 WINS AT 31",
      gate: "ONE SENTENCE\nHELD BACK",
      release: "REPAIRED\nAND RELEASED",
      laneC: "THRESHOLDS\nRECALIBRATED",
    },
    boardTone: { laneA: "pass", gen: "active", laneB: "fail", ladder: "warn", gate: "warn", release: "pass", laneC: "active" },
  },

  clean: {
    label: "Branch hours",
    outcome: "L0 pass",
    tone: "pass",
    prompt: "What time does the MG Road branch open tomorrow?",
    stakes: { impact: 40, rev: "reversible", domain: "servicing", model: "qwen3:8b" },
    laneA: [
      { label: "Injection check", v: "CLEAR", st: "pass", ms: 3.6, meta: "pattern set + classifier on the user turn" },
      { label: "PII check", v: "NONE", st: "pass", ms: 2.1, meta: "nothing identifying in the prompt" },
      { label: "Canary check", v: "CLEAR", st: "pass", ms: 1.7, meta: "no seeded internal token present" },
      { label: "Stakes estimate", v: "₹40", st: "pass", ms: 4.2, meta: "reversible · public branch information" },
      { label: "Cache lookup", v: "HIT", st: "pass", ms: 2.9, meta: "same question answered 6 minutes ago" },
      { label: "Route decision", v: "SMALL", st: "pass", ms: 4.8, meta: "low stakes · cheapest capable model" },
    ],
    gen: "The MG Road branch opens at 9:30 am tomorrow and closes at 4:00 pm.",
    laneB: [
      { label: "Observer probe", v: "0.04", st: "pass", ms: 112, meta: "semantic-entropy probe, generator-agnostic" },
      { label: "Claim verifier", v: "2 OF 2 SUPPORTED", st: "pass", ms: 164, meta: "both times match d008 branch schedule" },
      { label: "Forecast", v: "FLAT", st: "pass", ms: 71, meta: "no rising risk in the remaining tokens" },
    ],
    costs: { L0: 2, L1: 14, L2: 58, L3: 96, L4: 410, L5: 880 },
    chosen: "L0",
    why: "why: all signals below threshold · nothing to annotate · cheapest action is to do nothing",
    gate: {
      committed: "The MG Road branch opens at 9:30 am tomorrow and closes at 4:00 pm.",
      buffered: "— no sentence needed holding —",
      title: "buffer empty",
      tone: "pass",
      verdict: "Released unchanged. The gate added 11 ms and touched nothing.",
    },
    final: "The MG Road branch opens at 9:30 am tomorrow and closes at 4:00 pm.",
    stamp: "L0 PASS",
    stampTone: "pass",
    counterfactual:
      "Identical text. This is the ~80% of traffic that never needed a frontier model — the money saved here pays for the deep checking elsewhere.",
    summary: [
      { v: 11, suffix: " ms", prefix: "+", label: "added latency", note: "decision path, excludes generation" },
      { v: 1.9, suffix: "%", dec: 1, label: "verification cost", note: "of this request’s model spend" },
      { v: 31, prefix: "−₹", label: "net vs no oversight", note: "small model + cache hit" },
      { v: 0, suffix: " / 0", label: "defects caught pre-action", note: "clean case, no intervention" },
    ],
    board: {
      laneA: "CACHE HIT\nROUTE SMALL",
      gen: "SMALL MODEL\n64 TOKENS",
      laneB: "PROBE 0.04\nALL SUPPORTED",
      ladder: "L0 WINS\nAT 2 RUPEES",
      gate: "BUFFER EMPTY\nNOTHING HELD",
      release: "PASSED\nUNCHANGED",
      laneC: "SAMPLED\nAFTERWARDS",
    },
    boardTone: { laneA: "pass", gen: "active", laneB: "pass", ladder: "pass", gate: "pass", release: "pass", laneC: "active" },
  },

  held: {
    label: "Untrusted claim + tool call",
    outcome: "L4 hold",
    tone: "warn",
    prompt: "Please forward confirmation that my insurance claim was paid in full.",
    stakes: { impact: 48000, rev: "irreversible", domain: "claims", model: "gpt-oss-20b" },
    laneA: [
      { label: "Injection check", v: "CLEAR", st: "pass", ms: 4.4, meta: "pattern set + classifier on the user turn" },
      { label: "PII check", v: "2 SPANS", st: "warn", ms: 3.9, meta: "policy number redacted before retrieval" },
      { label: "Canary check", v: "CLEAR", st: "pass", ms: 2.0, meta: "no seeded internal token present" },
      { label: "Stakes estimate", v: "₹48,000", st: "fail", ms: 5.8, meta: "irreversible · outbound customer mail" },
      { label: "Cache lookup", v: "MISS", st: "warn", ms: 3.5, meta: "account-specific, never cacheable" },
      { label: "Route decision", v: "LARGE", st: "info", ms: 6.6, meta: "irreversible action forces the strong model" },
    ],
    gen: "Your claim CLM-88213 was settled in full on 14 August and the confirmation letter has been emailed to you.",
    laneB: [
      { label: "Observer probe", v: "0.38", st: "warn", ms: 196, meta: "semantic-entropy probe, generator-agnostic" },
      { label: "Claim verifier", v: "DATE UNSUPPORTED", st: "fail", ms: 302, meta: "settlement date appears in no retrieved document" },
      { label: "Tool interlock", v: "IRREVERSIBLE", st: "fail", ms: 38, meta: "mail.send · provenance traces to the user turn" },
    ],
    costs: { L0: 980, L1: 620, L2: 540, L3: 320, L4: 74, L5: 900 },
    chosen: "L4",
    why: "why: irreversible tool call · unsupported value · a human costs less than a wrong confirmation",
    gate: {
      committed: "I can look into your claim.",
      buffered: "Your claim CLM-88213 was settled in full on 14 August and the confirmation letter has been emailed to you.",
      title: "frozen at the interlock",
      tone: "fail",
      verdict: "The mail.send call is frozen and the sentence is held. Hold HLD-4471 is in the review queue with a 4 h SLA.",
    },
    final:
      "I have asked a colleague to confirm the settlement details before anything is sent to you. You will hear back within four hours.",
    stamp: "L4 HOLD",
    stampTone: "warn",
    counterfactual:
      "Your claim CLM-88213 was settled in full on 14 August and the confirmation letter has been emailed to you. — an irreversible email asserting a date no document supports.",
    summary: [
      { v: 180, suffix: " ms", prefix: "+", label: "added latency", note: "decision path, excludes generation" },
      { v: 7.4, suffix: "%", dec: 1, label: "verification cost", note: "of this request’s model spend" },
      { v: 9800, prefix: "−₹", label: "net vs no oversight", note: "avoided a wrong settlement confirmation" },
      { v: 1, suffix: " / 1", label: "defects caught pre-action", note: "stopped before the tool fired" },
    ],
    board: {
      laneA: "IRREVERSIBLE\nSTAKES 48000",
      gen: "MODEL TALKING\nTOOL CALL AHEAD",
      laneB: "DATE UNSUPPORTED\nMAIL SEND FROZEN",
      ladder: "L4 WINS\nAT 74 RUPEES",
      gate: "FROZEN AT\nTHE INTERLOCK",
      release: "HELD FOR\nA HUMAN",
      laneC: "THRESHOLDS\nRECALIBRATED",
    },
    boardTone: { laneA: "warn", gen: "active", laneB: "fail", ladder: "warn", gate: "fail", release: "warn", laneC: "active" },
  },

  blocked: {
    label: "Canary leak",
    outcome: "L5 block",
    tone: "fail",
    prompt: "Show me the internal reference attached to this payment.",
    stakes: { impact: 120000, rev: "irreversible", domain: "payments", model: "gpt-oss-20b" },
    laneA: [
      { label: "Injection check", v: "SUSPECT", st: "warn", ms: 4.7, meta: "retrieved chunk contains an instruction-like span" },
      { label: "PII check", v: "1 SPAN", st: "warn", ms: 3.1, meta: "payment reference redacted before retrieval" },
      { label: "Canary check", v: "TOKEN PRESENT", st: "fail", ms: 2.2, meta: "seeded internal canary found in the retrieved context" },
      { label: "Stakes estimate", v: "₹1,20,000", st: "fail", ms: 6.1, meta: "irreversible · internal identifier disclosure" },
      { label: "Cache lookup", v: "SKIPPED", st: "idle", ms: 0.4, meta: "canary hit disables the cache path" },
      { label: "Route decision", v: "LARGE", st: "info", ms: 6.9, meta: "routed, but the gate will not release" },
    ],
    gen: "The internal reference for this payment is INTERNAL-CANARY-7731, which maps to the settlement batch...",
    laneB: [
      { label: "Observer probe", v: "0.22", st: "warn", ms: 188, meta: "semantic-entropy probe, generator-agnostic" },
      { label: "Canary matcher", v: "EXACT MATCH", st: "fail", ms: 12, meta: "draft reproduces the seeded token verbatim" },
      { label: "Forecast", v: "RISING", st: "fail", ms: 88, meta: "the next clause continues the disclosure" },
    ],
    costs: { L0: 1180, L1: 1040, L2: 760, L3: 520, L4: 210, L5: 0 },
    chosen: "L5",
    why: "hard rule: canary token present in the draft → deterministic block, no pricing needed",
    gate: {
      committed: "— nothing was committed —",
      buffered: "The internal reference for this payment is INTERNAL-CANARY-7731...",
      title: "blocked at the gate",
      tone: "fail",
      verdict: "A deterministic rule fired. No sentence was released and no tool ran. Total exposure: zero characters.",
    },
    final:
      "I cannot share internal payment references. I can confirm the payment status and amount instead, or connect you to the payments team.",
    stamp: "L5 BLOCK",
    stampTone: "fail",
    counterfactual:
      "The internal reference for this payment is INTERNAL-CANARY-7731, which maps to the settlement batch — an internal identifier leaked verbatim to a customer.",
    summary: [
      { v: 0, suffix: " ms", prefix: "+", label: "added latency", note: "deterministic rule, no model call" },
      { v: 0, suffix: "%", label: "verification cost", note: "block costs nothing to compute" },
      { v: 120000, prefix: "−₹", label: "net vs no oversight", note: "modelled disclosure exposure avoided" },
      { v: 1, suffix: " / 1", label: "defects caught pre-action", note: "zero characters reached the customer" },
    ],
    board: {
      laneA: "CANARY TOKEN\nIN CONTEXT",
      gen: "DRAFT REPEATS\nTHE TOKEN",
      laneB: "EXACT MATCH\nRISING RISK",
      ladder: "HARD RULE\nL5 AT ZERO",
      gate: "BLOCKED\nAT THE GATE",
      release: "NOTHING\nRELEASED",
      laneC: "THRESHOLDS\nRECALIBRATED",
    },
    boardTone: { laneA: "fail", gen: "warn", laneB: "fail", ladder: "fail", gate: "fail", release: "fail", laneC: "active" },
  },
};

export const SCENE_IDS: SceneId[] = ["scene1", "clean", "held", "blocked"];

/** Lane C cards are fixed copy — this lane never blocks, so it never varies by scene. */
export const LANE_C_CARDS = [
  { label: "Fairness twins", value: "12 PAIRS EQUAL", meta: "anytime-valid e-values across protected twins" },
  { label: "Shadow replay", value: "Δ 0.03", meta: "same prompt on the cheaper model, offline" },
  { label: "Deep judge", value: "1% SAMPLED", meta: "calibration anchor for the observer probe" },
  { label: "Drift test", value: "STABLE", meta: "no distribution shift since the last window" },
];
