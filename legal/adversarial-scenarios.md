# Adversarial Scenario Analysis — SCCL v1 / Organization Charter

**Version:** 1.0-draft
**Status:** Working analysis, part of Issue #83's outstanding checklist ("the license structure holds up under adversarial scenarios"). This document is a structured first pass by the Organization's founders and is **not a substitute for review by legal counsel** — it exists so counsel has a starting map of known stress points rather than a blank page.

---

## Purpose

For each scenario: what an adversary tries, what in the current SCCL/Charter mechanics stops or slows it, and what residual risk remains (if any) that this document does not claim to have solved.

---

## Scenario 1 — Hostile Licensee

**1.1 Licensee disputes every Board decision to stall and exhaust the Organization.**

A Licensee facing revocation files a Contested Revocation Review petition (SCCL Article 6.8) purely to buy time, regardless of merit.

- *Holds:* The stay under 6.8(b) does not apply where the Board has invoked immediate revocation for documented bad-faith cure exploitation (6.4(b)/(c)) — a genuinely hostile Licensee with a real compliance history triggers that carve-out. At Stages 1–2, review is Board-run, not community-run, so there's no larger process to stall.
- *Residual risk:* At Stage 3+, a single contested review is a binding contributor vote (Charter B.6.3(c)) — cheap for a well-resourced Licensee to force, expensive in community attention each time. Nothing currently rate-limits how often the *same* Licensee can trigger review petitions. **Not fixed here** — worth a future amendment capping petition frequency per Licensee per rolling period.

**1.2 Licensee manipulates Revenue Basis reporting (SCCL 4.4) to underpay.**

- *Holds:* Audit Right (4.5) lets the Organization inspect records; underpayment beyond the Audit Tolerance Threshold shifts audit cost to the Licensee and feeds the Relationship Index (1.11), which can escalate future Revenue Basis tier severity (1.12) and shorten future Cure Periods (6.3(b)).
- *Residual risk:* Audit is capped at once per year (4.5) and requires 30 days' notice — a sophisticated Licensee has a known audit window to normalize books around. The "Affiliated Entities" test (1.12 note) requires >50% common control; a Licensee could structure revenue through a minority-owned (e.g., 49%) shell to argue it falls outside "Revenue on the Ecosystem" scope even while functionally controlling it. **Not fixed here** — the >50% bright-line control test is precise but gameable; a facts-and-circumstances "de facto control" backstop (similar to Charter B.2.8(f)'s approach for Employer Groups) would close this but isn't drafted yet.

**1.3 Licensee litigates instead of complying (Non-Aggression, SCCL 3.3).**

- *Holds:* Initiating IP litigation against the Organization or its contributors is itself a Non-Aggression breach (3.3(a)) and grounds for revocation (6.2(a)) — the Licensee cannot sue its way out of the License without triggering the License's own termination.
- *Residual risk:* None identified beyond ordinary litigation cost/time risk inherent to any license enforcement — this is a legal-counsel question (enforceability of non-aggression clauses varies by jurisdiction, hence 8.3's importance), not a drafting gap.

---

## Scenario 2 — Board / Governance Capture

**2.1 A single employer accumulates Governance Pool dominance through years of ordinary hiring.**

- *Holds:* Employer Concentration Limit (Charter B.2.8), active from Stage 4 onward, caps any Employer Group at 20% of the Pool and 1 Board seat, enforced by exclusion (not down-weighting) at each monthly recalculation. Affiliation Disclosure (c-points.md §1a) plus the Integrity Multiplier (§3.6) penalize concealment.
- *Residual risk:* B.2.8 only activates at Stage 4. Between the 500-Active-Contributor eligibility trigger and actual Stage 4 activation, the project sits at Stage 3, where Pool composition is advisory only (B.9.3) — so a concentrated employer bloc has no *binding* power yet, but could use its Stage-3 advisory weight to campaign against Stage 4 activation itself (Rolling Vote, B.9.4) indefinitely, since a Negative tally blocks Stage 4 regardless of contributor count (B.9.4(g)). **This is a real, not-fully-closed risk**: a large employer could rationally prefer to keep the project at Stage 3 (Board-controlled, its employees hold advisory influence and up to 1 elected seat) rather than let Stage 4 activate and trigger B.2.8's cap against it. Founders retain full Board authority through Stage 3, which limits the practical damage, but this is the mechanism worth flagging most prominently to legal counsel and to future Board members.

**2.2 Coordinated bloc removes a Board member via the Continuous Confidence Vote (Charter B.3.3) at low turnout.**

- *Holds:* Requires >50% of non-neutral votes AND ≥10% Pool participation — a small bloc cannot act alone without the rest of the Pool actively countering with "keep" votes.
- *Residual risk:* The 10% participation floor is low by design (to keep the mechanism usable at scale), which means a coordinated bloc of roughly 10–15% of the Pool, voting uniformly while the remaining ~85–90% stays passive (the normal state for most large communities), can clear the bar. This is a known trade-off in "health bar" style continuous-confidence systems generally, not unique to this draft. **Not fixed here** — a possible future mitigation is a minimum "keep" floor (e.g., replacement blocked if keep votes alone exceed some absolute threshold) rather than only a replace/non-neutral ratio; flagged for Board consideration, not drafted.

**2.3 Founder dies/retires before Stage 3, seat falls to a thin, capturable Pool.**

- *Holds:* Now addressed by Charter B.3.6 — Designated Successor holds the seat through a 12-month bridge, cannot be Licensee-affiliated (immutable), and the seat does not auto-convert into an immature Pool.
- *Residual risk:* Relies on the Founder actually filing a Designated Successor in advance. If neither Founder ever files one, B.3.6(d) falls back to Emergency Succession (B.7.2) — "5 most recent Active Contributors by contribution date," which has no Employer Group diversity requirement of its own. **Partial gap** — B.7.2 could be strengthened to require those 5 not share an Employer Group, matching B.2.8's logic; not drafted here since it's Stage-1/2-era (pre-Pool, pre-B.2.8 activation) and the founders filing a Designated Successor is the primary intended mitigation.

---

## Scenario 3 — Fee Formula Edge Cases

**3.1 Board sets K₁/K₂ punitively high to force a Licensee out entirely.**

- *Holds:* From Stage 3 onward, Fee Formula changes require community ratification (Charter B.6.4) — the Board cannot unilaterally weaponize the formula against a disfavored Licensee once past the earliest stage. A Licensee can also always exit to AGPLv3 compliance instead of paying (SCCL 2.5, 4.2's negotiation clause A.6).
- *Residual risk:* At Stages 1–2, this ratification requirement doesn't apply yet (B.6.4 activates at Stage 3) — so a punitive fee is theoretically possible pre-Stage-3, though there are by definition few or no commercial Licensees yet at that stage for it to matter (Article 2.1's 15-Active-Contributor activation threshold gates when licenses can even issue). Low practical risk given the ordering of thresholds, but worth legal counsel's eyes.

**3.2 Licensee structures around the Revenue Basis tiers.**

- Covered under 1.2 above (Affiliated Entities >50% control test).

**3.3 Inflation factor exploited during high-inflation periods to justify runaway fee growth.**

- *Holds:* A.3's floor (`inflation_factor` never below 1.0) prevents deflation abuse; the buffer is capped in the Charter (2–5%) and requires a Charter amendment to change, not a unilateral Board act.
- *Residual risk:* None identified beyond the CPI-index selection itself being a one-time Board choice (A.3) that could favor an index understating true cost growth — a legal/economic counsel question, not a structural loophole.

---

## Summary of Unresolved Residual Risks

| # | Risk | Severity | Status |
|---|------|----------|--------|
| 1 | No rate limit on repeated Contested Revocation Review petitions by the same Licensee (1.1) | Low–Medium | Not fixed |
| 2 | Affiliated-Entities >50% control test is a bright line, gameable via minority-stake structuring (1.2, 3.2) | Medium | Not fixed |
| 3 | Employer bloc may rationally oppose Stage 4 activation to avoid B.2.8's cap (2.1) | **High** | Not fixed — most significant open item |
| 4 | Continuous Confidence Vote's 10% participation floor allows small coordinated blocs to act at low turnout (2.2) | Medium | Not fixed |
| 5 | Emergency Succession (B.7.2) has no Employer Group diversity requirement if no Designated Successor is filed (2.3) | Low–Medium | Partial (mitigated by encouraging Designated Successor filing) |

None of these risks are fabricated to look resolved — they are named here specifically because SCCL/Charter drafting alone cannot close them with confidence; they need either further design iteration, or a judgment call from actual legal/governance counsel about acceptable risk tolerance, before Issue #83 can honestly close its "adversarial scenarios" checklist item.

---

*This document is maintained alongside SCCL-v1.md and organization-charter.md. Findings here should inform, not substitute for, the legal counsel review required to close Issue #83.*
