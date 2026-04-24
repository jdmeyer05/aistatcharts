import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Disclaimer — AI Statcharts",
  description:
    "AI Statcharts is an analytical research tool. Nothing on the platform constitutes financial, investment, tax, or legal advice.",
  robots: { index: true, follow: true },
};

const LAST_UPDATED = "April 23, 2026";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card space-y-3">
      <h2 className="text-sm font-bold uppercase tracking-wider text-accent">{title}</h2>
      <div className="text-sm leading-relaxed space-y-2 text-text">{children}</div>
    </section>
  );
}

export default function DisclaimerPage() {
  return (
    <main className="max-w-3xl mx-auto px-4 py-8 space-y-5">
      <header className="card">
        <div className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
          Last updated · {LAST_UPDATED}
        </div>
        <h1 className="text-2xl font-bold mt-1">Disclaimer & Terms of Use</h1>
        <p className="text-sm text-text-muted mt-2 leading-relaxed">
          By accessing or using AI Statcharts (&ldquo;the Platform&rdquo;), you acknowledge that
          you have read, understood, and agree to be bound by the following terms. If you
          do not agree, do not use the Platform.
        </p>
      </header>

      <Section title="1. Nature of the Platform">
        <p>
          AI Statcharts is an <strong>analytical and research tool</strong> providing
          quantitative market analysis, scenario modeling, and AI-generated insights to
          assist users in evaluating financial data and market conditions. It is{" "}
          <strong>not</strong> a registered investment adviser, broker-dealer, or
          financial planning service.
        </p>
      </Section>

      <Section title="2. Not Financial Advice">
        <p>
          <strong>
            Nothing on this Platform constitutes financial, investment, tax, or legal
            advice.
          </strong>{" "}
          All content — including AI-generated stock analyses, macro regime probabilities,
          scenario outputs, reinforcement-learning strategy backtests, portfolio impact
          estimates, price targets, risk assessments, confidence scores, Trump Decoder
          interpretations, and any output from Claude, GPT, Gemini, or Grok models — is
          provided for <strong>informational and educational purposes only</strong>. A
          &ldquo;Buy,&rdquo; &ldquo;Sell,&rdquo; or similar label is a model output and
          does <strong>not</strong> constitute a solicitation or recommendation to buy,
          sell, or hold any security.
        </p>
      </Section>

      <Section title="3. AI Model Limitations">
        <p>The Platform uses multiple AI models from Anthropic, OpenAI, Google, and xAI. You acknowledge:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li>AI models can produce <strong>inaccurate, incomplete, or misleading outputs</strong>.</li>
          <li>Models may <strong>hallucinate</strong> facts, tickers, analyst opinions, or data points that do not exist.</li>
          <li>Scores, price targets, and recommendations are <strong>probabilistic estimates</strong>, not predictions.</li>
          <li>Past model performance does <strong>not guarantee future results</strong>.</li>
          <li>Social-media-derived sentiment reflects <strong>unverified posts</strong>, not validated research.</li>
          <li>Model outputs may <strong>conflict with each other</strong>; consensus does not imply correctness.</li>
        </ul>
      </Section>

      <Section title="4. Data Accuracy">
        <p>
          The Platform aggregates data from third-party sources including Polygon, FRED,
          EIA, Finnhub, CFTC, SEC EDGAR, Polymarket, Massive, and others. Third-party data
          may be <strong>delayed, incomplete, or incorrect</strong>. FOMC projections and
          Beige Book summaries may become <strong>stale between release cycles</strong>.
          Polymarket odds represent <strong>prediction-market consensus</strong>, not
          objective probability. Real-time feeds may experience outages or delays.
        </p>
      </Section>

      <Section title="5. Backtests & Strategy Research">
        <p>
          Algorithmic backtests, Deflated Sharpe Ratios, walk-forward validation,
          reinforcement-learning strategies, and confluence analyses provide{" "}
          <strong>evidence but not proof</strong> of a strategy&apos;s viability. They are
          computed in a simulated environment that cannot perfectly replicate live market
          conditions (liquidity, partial fills, market impact, halts, gaps). Strategies
          that appear profitable in backtesting frequently <strong>fail in live
          trading</strong>. The Platform does <strong>not</strong> execute trades or
          connect to any brokerage account. You should <strong>never</strong> deploy a
          strategy based solely on Platform output without independent validation.
        </p>
      </Section>

      <Section title="6. Portfolio & Risk Analysis">
        <p>
          VaR, CVaR, factor decompositions, Monte Carlo simulations, Greeks, and
          volatility-surface analyses are based on statistical models with inherent
          assumptions. Factor betas are estimated from historical data and may be{" "}
          <strong>unstable</strong>. Confidence intervals assume distributions that may
          not hold during <strong>extreme market events</strong>. Correlation structures
          can <strong>break down during crises</strong>. Model outputs are not a
          substitute for qualitative judgment.
        </p>
      </Section>

      <Section title="7. User Responsibilities">
        <p>By using the Platform you agree to:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li><strong>Conduct your own due diligence</strong> before any investment decision.</li>
          <li><strong>Not rely solely</strong> on Platform outputs for trading, investment, or financial planning.</li>
          <li><strong>Consult a qualified financial advisor</strong> before acting on Platform information.</li>
          <li>Understand that investing carries risk, including loss of principal.</li>
          <li>Use the Platform in compliance with all applicable laws and regulations.</li>
          <li>Not redistribute, resell, or commercially exploit the Platform&apos;s outputs without written permission.</li>
        </ul>
      </Section>

      <Section title="8. No Guarantee of Availability">
        <p>
          The Platform is provided on an &ldquo;as-is&rdquo; basis. We do not guarantee
          continuous, uninterrupted, or error-free operation, that every feature will
          function at all times, that third-party APIs will remain available, or any
          specific level of uptime or performance.
        </p>
      </Section>

      <Section title="9. Limitation of Liability">
        <p>
          To the maximum extent permitted by law, the creators, operators, and
          contributors to AI Statcharts shall <strong>not be liable</strong> for any
          direct, indirect, incidental, special, consequential, or exemplary damages —
          including loss of profits, goodwill, data, or other intangible losses — arising
          from your use of the Platform, any trading or investment decisions made from
          Platform outputs, errors or omissions in data or AI-generated content,
          unauthorized access to your data, or any third-party conduct on or related to
          the Platform.
        </p>
      </Section>

      <Section title="10. Intellectual Property">
        <p>
          All code, design, methodology, and content on AI Statcharts is proprietary. You
          may not reverse-engineer the Platform&apos;s algorithms or models, scrape or
          bulk-download data or outputs, use the Platform&apos;s outputs to train
          competing AI models, or claim the Platform&apos;s outputs as your own original
          research.
        </p>
      </Section>

      <Section title="11. Privacy">
        <p>
          The Platform stores your email address for authentication (via Supabase),
          subscription and token-ledger state for billing (via Supabase + Stripe), and
          session analytics state (watchlists, saved preferences). We do <strong>not
          sell or share your personal data</strong>. Logs containing identifiable data
          are retained for a maximum of 90 days. See our Privacy Policy for full details
          once published.
        </p>
      </Section>

      <Section title="12. Changes to Terms">
        <p>
          We reserve the right to modify these terms at any time. Continued use of the
          Platform after changes constitutes acceptance of the revised terms. The
          &ldquo;Last updated&rdquo; date at the top of this page indicates when terms
          were last revised.
        </p>
      </Section>

      <Section title="13. Governing Law">
        <p>
          These terms are governed by the laws of the State of Texas, United States,
          without regard to conflict-of-law principles.
        </p>
      </Section>

      <footer className="card text-center">
        <p className="text-sm text-text-muted">
          Questions? Contact{" "}
          <a href="mailto:jdmeyer05@gmail.com" className="text-accent hover:underline">
            jdmeyer05@gmail.com
          </a>
          .
        </p>
      </footer>
    </main>
  );
}
