import type {
  Action,
  InterlockEventMap,
  InterlockEventName,
  OpenAIChunk,
  ParsedFrame,
} from "./contracts";

const actions = new Set<Action>([
  "L0_pass",
  "L1_annotate",
  "L2_repair",
  "L3_reroute",
  "L4_hold",
  "L5_block",
]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const isIndex = (value: unknown): value is number =>
  Number.isInteger(value) && (value as number) >= 0;
const isOptionalString = (value: unknown) => value === undefined || value === null || typeof value === "string";
const isOptionalNumber = (value: unknown) => value === undefined || isFiniteNumber(value);

function malformed(subject: string): ParsedFrame {
  return {
    kind: "diagnostic",
    code: "malformed-frame",
    message: `${subject} did not match the required event schema`,
  };
}

export function parseOpenAIChunk(data: unknown): ParsedFrame {
  if (!isRecord(data)) return malformed("OpenAI chunk");
  if (data.id !== undefined && typeof data.id !== "string") return malformed("OpenAI chunk");
  if (data.choices !== undefined) {
    if (!Array.isArray(data.choices)) return malformed("OpenAI chunk");
    for (const choice of data.choices) {
      if (!isRecord(choice)) return malformed("OpenAI chunk");
      if (choice.finish_reason !== undefined && choice.finish_reason !== null && typeof choice.finish_reason !== "string") {
        return malformed("OpenAI chunk");
      }
      if (choice.delta !== undefined) {
        if (!isRecord(choice.delta)) return malformed("OpenAI chunk");
        const content = choice.delta.content;
        if (content !== undefined && content !== null && typeof content !== "string") return malformed("OpenAI chunk");
      }
    }
  }
  return { kind: "openai", data: data as unknown as OpenAIChunk };
}

export function parseInterlockEvent<Name extends InterlockEventName>(
  event: Name,
  data: unknown,
): ParsedFrame {
  if (!isRecord(data)) return malformed(event);

  let valid = false;
  if (event === "interlock.stakes") {
    valid = isFiniteNumber(data.impact_inr) && data.impact_inr >= 0 &&
      (data.reversibility === "reversible" || data.reversibility === "costly" || data.reversibility === "irreversible") &&
      typeof data.domain === "string" && data.domain.length > 0 &&
      (data.mode === "buffered" || data.mode === "unbuffered") &&
      isOptionalString(data.stakes_id) && isOptionalString(data.route_reason) && isOptionalString(data.model_served);
  } else if (event === "interlock.signal") {
    valid = isIndex(data.sentence_idx) && typeof data.name === "string" && data.name.length > 0 &&
      (data.prob === null || (isFiniteNumber(data.prob) && data.prob >= 0 && data.prob <= 1));
  } else if (event === "interlock.decision") {
    valid = typeof data.decision_id === "string" && data.decision_id.length > 0 &&
      (isIndex(data.sentence_idx) || data.sentence_idx === -1) &&
      actions.has(data.action as Action) &&
      isFiniteNumber(data.chosen_loss) &&
      (data.runner_up === undefined || data.runner_up === null || actions.has(data.runner_up as Action)) &&
      isOptionalNumber(data.margin) && isOptionalString(data.counterfactual) &&
      isOptionalString(data.hard_rule) && (data.degraded === undefined || typeof data.degraded === "boolean");
  } else {
    valid = typeof data.hold_id === "string" && data.hold_id.length > 0 &&
      (data.kind === "response" || data.kind === "tool_call") &&
      typeof data.reason === "string" && data.reason.length > 0 &&
      isOptionalString(data.tool) &&
      (data.sentence_idx === undefined || data.sentence_idx === null || isIndex(data.sentence_idx));
  }

  if (!valid) return malformed(event);
  return { kind: "interlock", event, data: data as unknown as InterlockEventMap[Name] } as ParsedFrame;
}
