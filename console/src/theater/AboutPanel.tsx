import { MicroLabel } from "./primitives";
import { color, font, radius } from "./tokens";

/**
 * The plain-language explanation of the product, plus the work it is built on.
 * Written for someone who has never seen an AI system diagram: no jargon in the
 * first half, and every mechanism in the second half names its source.
 */

const STAGES_IN_PLAIN_WORDS = [
  {
    n: "01",
    title: "Before the model sees anything",
    body:
      "We read the question and ask a simple thing: how much does this one matter? A branch's opening " +
      "time is worth a few rupees of care. A payment confirmation is worth thousands. That single number " +
      "decides both how much computing power we spend answering, and how hard we check the answer.",
  },
  {
    n: "02",
    title: "The model answers",
    body:
      "The AI model writes its reply exactly as it normally would. Interlock does not change the model, " +
      "retrain it, or look inside it. It sits in front of it, like a switchboard the call passes through.",
  },
  {
    n: "03",
    title: "We check while it is still talking",
    body:
      "A second, much smaller model reads along and flags sentences that look invented. At the same time " +
      "we check each factual claim against the bank's own documents. This happens during the answer, not " +
      "after it, so it costs almost no extra waiting time.",
  },
  {
    n: "04",
    title: "We price every option",
    body:
      "Six possible responses — from doing nothing to blocking outright — are each priced in rupees: what " +
      "it would cost if this answer is wrong, what it costs to annoy a customer needlessly, and what the " +
      "checking itself costs. The cheapest safe option wins. It is arithmetic, not opinion.",
  },
  {
    n: "05",
    title: "We hold one sentence back",
    body:
      "Live television runs on a few seconds' delay so a producer can catch a problem before it airs. We " +
      "do the same with one sentence: the customer is always reading the sentence before the one we are " +
      "still checking, so a bad sentence can be fixed before anybody sees it.",
  },
  {
    n: "06",
    title: "The customer sees the answer",
    body:
      "Either the original text, or a corrected version, or a note that a colleague is checking. Whatever " +
      "happened is stamped on the answer, so nobody has to guess whether it was touched.",
  },
  {
    n: "07",
    title: "Afterwards, off to one side",
    body:
      "Once the customer has their answer, we keep working quietly: does the system treat similar customers " +
      "the same way, would a cheaper model have done as well, and are our own checks still accurate? None " +
      "of this delays anyone.",
  },
];

const LADDER_IN_PLAIN_WORDS = [
  { level: "L0", name: "Send it as it is", body: "Nothing looked wrong. Most questions end here." },
  { level: "L1", name: "Add a note", body: "Ship the answer, but cite the source or flag the uncertain part." },
  { level: "L2", name: "Fix the sentence", body: "One sentence was wrong; rewrite that sentence, keep the rest." },
  { level: "L3", name: "Ask a better model", body: "Start again with a stronger model and better documents." },
  { level: "L4", name: "Ask a person", body: "Too costly to get wrong. It waits for a human, and says so." },
  { level: "L5", name: "Stop it", body: "A hard rule was broken — an internal reference was about to leak. Nothing is sent." },
];

interface Citation {
  mechanism: string;
  source: string;
  href?: string;
  takeaway: string;
}

const PERFORMANCE: Citation[] = [
  {
    mechanism: "Spotting confident nonsense",
    source: "Farquhar, Kossen, Kuhn & Gal, “Detecting hallucinations in large language models using semantic entropy”, Nature 630:625–630 (2024)",
    href: "https://www.nature.com/articles/s41586-024-07421-0",
    takeaway: "Measures uncertainty over meanings rather than wording. We use it offline to generate training labels — never on the live path, because it needs ten answers to one question.",
  },
  {
    mechanism: "Making that check cheap enough to always run",
    source: "Kossen et al., “Semantic Entropy Probes”, arXiv:2406.15927 (2024)",
    href: "https://arxiv.org/abs/2406.15927",
    takeaway: "The same signal is recoverable from a single pass, so it can run on every request instead of a sample.",
  },
  {
    mechanism: "Watching the answer as it is typed",
    source: "Obeso, Arditi, Ferrando, Freeman, Holmes & Nanda, arXiv:2509.03531 (2025)",
    href: "https://arxiv.org/abs/2509.03531",
    takeaway: "Token-level probes scaled to a 70B model, and labels that transfer between model families.",
  },
  {
    mechanism: "Checking someone else's model without opening it",
    source: "O'Neill, Chalnev, Zhao, Kirkby & Jayasekara, arXiv:2507.23221 (2025)",
    href: "https://arxiv.org/abs/2507.23221",
    takeaway: "A small observer model detects another model's hallucinations from its own reading of the text. This is what lets Interlock sit in front of any provider.",
  },
  {
    mechanism: "Checking claims against the documents",
    source: "Tang, Laban et al., “MiniCheck”, EMNLP 2024",
    href: "https://arxiv.org/abs/2404.10774",
    takeaway: "A 770M-parameter checker reaching GPT-4 fact-checking accuracy at a fraction of the cost. It also points at the exact span that is wrong, which is what makes repair possible.",
  },
  {
    mechanism: "Sounding more certain than it should",
    source: "Ji et al., arXiv:2503.14477 (2025)",
    href: "https://arxiv.org/abs/2503.14477",
    takeaway: "The gap between how confident an answer sounds and how confident it should be predicts hallucination. We report that gap per answer.",
  },
  {
    mechanism: "Turning scores into promises",
    source: "Mohri & Hashimoto, “Language Models with Conformal Factuality Guarantees”, ICML 2024",
    href: "https://arxiv.org/abs/2402.10978",
    takeaway: "Statistical calibration, so a claim like “at most 1% of shipped answers are ungrounded, at 90% confidence” is defensible rather than decorative.",
  },
];

const SPEED: Citation[] = [
  {
    mechanism: "Cheap check first, expensive check rarely",
    source: "Anthropic, “Next-generation Constitutional Classifiers”, arXiv:2601.04603, and Cost-Effective Constitutional Classifiers via Representation Re-use",
    takeaway: "A shipped system where a light probe screens everything and escalates only when needed, at around 1% compute overhead. We copy the shape and point it at correctness and cost.",
  },
  {
    mechanism: "Checking while the answer streams",
    source: "“SentGuard”, arXiv:2606.02041 (2026)",
    takeaway: "A waiting-buffer design measured at 36 ms of added delay, against 576 ms for checking after the fact. We extend it from safety to groundedness, and to repairing rather than blocking.",
  },
  {
    mechanism: "Predicting where the answer is heading",
    source: "Kavumba et al., “Predict, Don't React: Value-Based Safety Forecasting for LLM Streaming”, arXiv:2604.03962 (2026)",
    takeaway: "Judge the risk of what is about to be said, not only what was said. We re-point it at truthfulness and cost.",
  },
];

const COST: Citation[] = [
  {
    mechanism: "Choosing the model before paying for one",
    source: "Ong et al., “RouteLLM”, ICLR 2025; Chen, Zaharia & Zou, “FrugalGPT”",
    href: "https://arxiv.org/abs/2406.18665",
    takeaway: "Deciding difficulty up front beats trying the cheap model first and escalating. This is the money that pays for the checking.",
  },
  {
    mechanism: "Answer, escalate, or ask a human — as one decision",
    source: "“Cascaded Language Models for Cost-Effective Human–AI Decision-Making”, NeurIPS 2025",
    takeaway: "The academic backing for our central claim: routing and guarding are the same decision, not two systems.",
  },
];

const RESPONSIBILITY: Citation[] = [
  {
    mechanism: "Testing for unequal treatment",
    source: "Tamkin et al. (Anthropic), arXiv:2312.03689",
    href: "https://arxiv.org/abs/2312.03689",
    takeaway: "Ask the same question with the customer's demographics varied and compare the answers. We run it on sampled real traffic rather than invented scenarios.",
  },
  {
    mechanism: "Monitoring continuously without crying wolf",
    source: "Johari, Koomen, Pekelis & Walsh, “Always Valid Inference”, Operations Research 70(3), 2022",
    takeaway: "Statistics designed to be checked continuously. Ordinary significance tests, peeked at repeatedly, manufacture false alarms.",
  },
  {
    mechanism: "Stopping unsafe actions, not unsafe words",
    source: "Debenedetti et al., “Defeating Prompt Injections by Design” (CaMeL), arXiv:2503.18813 (Google DeepMind, 2025); OWASP LLM06:2025",
    href: "https://arxiv.org/abs/2503.18813",
    takeaway: "Track where an instruction came from and check the policy before any tool runs. Irreversible actions triggered by untrusted text freeze and wait for a person.",
  },
  {
    mechanism: "Catching leaks with a marked note",
    source: "OWASP-referenced canary-token mitigation for system-prompt leakage",
    takeaway: "A secret marker is planted in our own documents. If it ever appears in an answer, that is a certainty, not a probability, and the answer is stopped with no model involved.",
  },
  {
    mechanism: "Preferring rules to judgement",
    source: "AWS Bedrock Automated Reasoning checks (generally available, August 2025)",
    takeaway: "Where a deterministic check exists, use it instead of asking another model. It is cheaper and it cannot be talked out of its answer.",
  },
];

const OURS = [
  {
    title: "One stakes estimate, two budgets",
    body:
      "Everyone else builds the cost router and the safety checker as separate systems with separate tuning. " +
      "Both are answering the same question — how much does this request matter — so we compute it once and " +
      "spend both budgets from it. That is why the checking pays for itself instead of being a tax.",
  },
  {
    title: "Risk priced in one currency",
    body:
      "Most tools hand an operator a wall of incomparable numbers and a threshold file. We convert every " +
      "signal into expected loss in rupees, so choosing between “send”, “fix” and “ask a human” is arithmetic " +
      "a risk officer can review, not an engineer's preference.",
  },
  {
    title: "Reporting waste, not spend",
    body:
      "Every gateway tells you what you spent. We also report what you wasted: capability the question never " +
      "needed, and the retries and human escalations caused by a bad answer, charged back to that answer.",
  },
];

export function AboutPanel() {
  return (
    <section
      style={{
        position: "relative",
        zIndex: 2,
        maxWidth: "980px",
        margin: "0 auto",
        padding: "34px 34px 80px",
        display: "flex",
        flexDirection: "column",
        gap: "34px",
      }}
      aria-label="About Interlock"
    >
      <div>
        <MicroLabel tone={color.accent}>What this is</MicroLabel>
        <h2 style={{ margin: "10px 0 0", font: `600 34px/1.1 ${font.sans}`, letterSpacing: "-.03em" }}>
          A safety and cost control room for AI answers
        </h2>
        <p style={{ margin: "16px 0 0", font: `400 16px/1.75 ${font.sans}`, color: color.textSoft }}>
          A bank puts an AI assistant in front of customers. Most questions are harmless — branch timings,
          balances, how to reset a card. A few are not: a made-up penalty clause, a settlement date no document
          supports, an email that should never have been sent. The hard part is that both kinds arrive through
          the same box, and you cannot tell which is which until the answer already exists.
        </p>
        <p style={{ margin: "14px 0 0", font: `400 16px/1.75 ${font.sans}`, color: color.textSoft }}>
          Interlock sits between the assistant and the customer. For every question it decides two things at
          once: <strong style={{ color: color.text }}>how much computing power this deserves</strong>, and{" "}
          <strong style={{ color: color.text }}>how hard the answer should be checked</strong>. Because most
          traffic is cheap to answer, the money saved there pays for the deep checking on the few requests that
          need it. Oversight funds itself instead of being a cost centre.
        </p>
        <p style={{ margin: "14px 0 0", font: `400 16px/1.75 ${font.sans}`, color: color.textDim }}>
          It does not replace or retrain the AI model. Any model can sit behind it — the assistant does not know
          Interlock is there, and neither does the customer, unless something needed to be fixed.
        </p>
      </div>

      <Card title="What happens to one question" eyebrow="The seven stages you can watch in the trace">
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {STAGES_IN_PLAIN_WORDS.map((stage) => (
            <div key={stage.n} style={{ display: "flex", gap: "16px" }}>
              <span style={{ font: `700 14px ${font.mono}`, color: color.accent, flex: "none", width: "26px" }}>
                {stage.n}
              </span>
              <div>
                <div style={{ font: `600 14px ${font.sans}` }}>{stage.title}</div>
                <p style={{ margin: "6px 0 0", font: `400 14px/1.7 ${font.sans}`, color: color.textDim }}>
                  {stage.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="What it can do about a bad answer" eyebrow="Six options, cheapest safe one wins">
        <p style={{ margin: "0 0 16px", font: `400 14px/1.7 ${font.sans}`, color: color.textDim }}>
          Blocking everything suspicious would make the assistant useless; blocking nothing makes it dangerous.
          So there is a ladder, and each rung is priced before one is chosen.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {LADDER_IN_PLAIN_WORDS.map((rung) => (
            <div
              key={rung.level}
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: "14px",
                padding: "11px 14px",
                borderRadius: radius.panel,
                border: `1px solid ${color.line}`,
                background: "rgba(230,225,215,.02)",
              }}
            >
              <span style={{ font: `700 13px ${font.mono}`, color: color.accent, width: "26px", flex: "none" }}>
                {rung.level}
              </span>
              <span style={{ font: `600 13px ${font.sans}`, width: "170px", flex: "none" }}>{rung.name}</span>
              <span style={{ font: `400 13px/1.6 ${font.sans}`, color: color.textDim }}>{rung.body}</span>
            </div>
          ))}
        </div>
      </Card>

      <Card title="How you can tell whether it works" eyebrow="What the numbers on the evidence page mean">
        <dl style={{ margin: 0, display: "flex", flexDirection: "column", gap: "14px" }}>
          {[
            [
              "Pre-action catch rate",
              "Of the things that went wrong, how many were stopped before a customer read them or a tool acted on them. This is the number the whole system exists to move.",
            ],
            [
              "Added p95 latency",
              "How much slower the assistant feels because Interlock is there. Measured, and kept under a tenth of a second.",
            ],
            [
              "Verification cost",
              "What the checking costs, as a share of what the AI model itself costs.",
            ],
            [
              "Net spend change",
              "Whether the whole arrangement saves money once the cheaper routing is counted against the checking.",
            ],
            [
              "Ungrounded escapes",
              "How often an unsupported claim still reaches a customer, with a statistical confidence level attached.",
            ],
            [
              "False interventions",
              "How often we interfered with an answer that was fine. This is the cost of being careful, and we publish it even when it is unflattering.",
            ],
          ].map(([term, definition]) => (
            <div key={term}>
              <dt style={{ font: `600 13px ${font.sans}`, color: color.text }}>{term}</dt>
              <dd style={{ margin: "5px 0 0", font: `400 13px/1.7 ${font.sans}`, color: color.textDim }}>
                {definition}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        <div>
          <MicroLabel tone={color.accent}>The research this is built on</MicroLabel>
          <h3 style={{ margin: "10px 0 0", font: `600 22px ${font.sans}` }}>Published work we re-implement</h3>
          <p style={{ margin: "10px 0 0", maxWidth: "700px", font: `400 14px/1.7 ${font.sans}`, color: color.textDim }}>
            Almost every mechanism here is somebody else's published result, re-implemented and pointed at this
            problem. Where that is the case we cite it rather than claim it.
          </p>
        </div>
        <CitationGroup heading="Catching answers that are confidently wrong" citations={PERFORMANCE} />
        <CitationGroup heading="Staying out of the customer's way" citations={SPEED} />
        <CitationGroup heading="Spending in proportion to what is at stake" citations={COST} />
        <CitationGroup heading="Fairness, leaks and unsafe actions" citations={RESPONSIBILITY} />
      </div>

      <Card title="What is genuinely ours" eyebrow="Labelled honestly">
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {OURS.map((item) => (
            <div key={item.title}>
              <div style={{ font: `600 14px ${font.sans}`, color: color.accent }}>{item.title}</div>
              <p style={{ margin: "6px 0 0", font: `400 14px/1.7 ${font.sans}`, color: color.textDim }}>{item.body}</p>
            </div>
          ))}
        </div>
      </Card>
    </section>
  );
}

function Card({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow: string;
  children: React.ReactNode;
}) {
  return (
    <article
      style={{
        borderRadius: radius.card,
        border: `1px solid ${color.line}`,
        background: color.bgPanel,
        padding: "24px 26px",
      }}
    >
      <MicroLabel tone={color.accent}>{eyebrow}</MicroLabel>
      <h3 style={{ margin: "10px 0 18px", font: `600 22px ${font.sans}`, letterSpacing: "-.02em" }}>{title}</h3>
      {children}
    </article>
  );
}

function CitationGroup({ heading, citations }: { heading: string; citations: Citation[] }) {
  return (
    <article
      style={{
        borderRadius: radius.card,
        border: `1px solid ${color.line}`,
        background: color.bgPanel,
        padding: "20px 22px",
      }}
    >
      <h4 style={{ margin: "0 0 14px", font: `600 15px ${font.sans}` }}>{heading}</h4>
      <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        {citations.map((citation) => (
          <div key={citation.mechanism} style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <div style={{ font: `600 13px ${font.sans}` }}>{citation.mechanism}</div>
            <div style={{ font: `400 12px/1.6 ${font.mono}`, color: color.textDim }}>
              {citation.href ? (
                <a
                  href={citation.href}
                  target="_blank"
                  rel="noreferrer noopener"
                  style={{ color: color.accent, textDecoration: "none", borderBottom: `1px solid ${color.line}` }}
                >
                  {citation.source}
                </a>
              ) : (
                citation.source
              )}
            </div>
            <p style={{ margin: 0, font: `400 13px/1.7 ${font.sans}`, color: color.textSoft }}>{citation.takeaway}</p>
          </div>
        ))}
      </div>
    </article>
  );
}
