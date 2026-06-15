# Organization Charter

> **Part of the [Sovereign Commons Commercial License (SCCL) v1.0](SCCL-v1.md)**
>
> **Status:** Working draft. Subject to legal review and community feedback.

---

### B.1 — Purpose and Identity

**B.1.1** The Organization exists to steward the Software and its ecosystem for the benefit of contributors and users, not corporations or investors. It is a **non-profit foundation** structured to resist capture by any single entity, corporate or individual.

**B.1.2** The Organization's governance evolves with its community. In its early stages, the Board governs with full authority — because a nascent project cannot afford governance paralysis. As the community grows, contributors earn progressively more governance power, until the Board serves at the community's pleasure. This progression is formalized in B.9 (Governance Stages).

**B.1.3** The Organization shall be incorporated as a **501(c)(3) public charity** or equivalent non-profit legal structure in the applicable jurisdiction. The 501(c)(3) structure ensures: (a) donations are tax-deductible for individual supporters; (b) the Organization cannot primarily serve corporate interests; (c) no individual or entity may profit from the Organization's dissolution.

### B.2 — Membership

**B.2.1 Contributors.** Any individual who has authored at least one merged contribution to the Software (as evidenced by the project's version control history and signed off via DCO) is a Contributor. Contributors are the primary stakeholders of the Organization.

**B.2.2 Active Contributors.** A Contributor is "Active" if they have authored at least one merged contribution within the preceding twenty-four (24) months. Only Active Contributors may exercise governance rights available at the current Governance Stage.

**B.2.3 Corporate Contribution Exclusion.** Contributions submitted on behalf of a commercial licensee — including those fulfilling the contribution-back obligation under SCCL Article 5, signed under the CLA, or authored in the scope of employment with a licensee — do **not** count toward an individual's Active Contributor status for governance purposes. Such contributions satisfy the licensee's contractual obligations but confer no governance rights.

**B.2.4 Individual Contributions by Licensee Employees.** An individual employed by a commercial licensee may qualify as an Active Contributor if and only if: (a) the contribution is signed off via DCO (not CLA); (b) the contribution is made outside the scope of their employment; and (c) the contribution is not fulfilling any part of their employer's SCCL Article 5 obligations. The individual's governance rights are personal and may not be directed or influenced by their employer.

**B.2.5 Contribution Points (C-Points).** Each Active Contributor accumulates **Contribution Points (C-points)** — a weighted composite score reflecting the quality, scope, and nature of their contributions. C-points are used to rank contributors for Governance Pool membership (B.2.6) and other governance purposes. The methodology for calculating C-points — including the variables considered and their respective weights — is defined in the [C-Points Methodology](c-points.md). C-points are recalculated continuously as contributions are merged.

**B.2.6 Governance Pool.** At Governance Stage 4, the governance body consists of the **Governance Pool**: the top **one thousand (1000)** Active Contributors, ranked by C-points (B.2.5), excluding corporate contributions per B.2.3. Only members of the Governance Pool may initiate votes, cast votes, and nominate or stand as candidates for Board seats at Stage 4. The Pool is recalculated continuously as C-points change and Active status changes. At Stages 1–3, all Active Contributors hold whatever governance rights the current stage provides (the 1000-member cap applies only at Stage 4).

**B.2.7 No Corporate Membership Tiers.** The Organization does not sell board seats, governance influence, or voting rights. Commercial licensees fund the ecosystem through the Annual License Fee (SCCL Article 4); this entitles them to use the Software commercially — not to govern it.

### B.3 — Governing Board

**B.3.1 Composition.** The Board shall consist of **five (5) to nine (9) members**, with the exact number set by Board resolution. The Board is composed as follows:

  (a) **Founder Seats (up to 2)** — Appointed by the founding members of the Organization. Founder seats convert to Pool-Elected seats when the founder steps down, is removed by Pool vote, or after a maximum of **ten (10) years** from the Organization's incorporation, whichever comes first.

  (b) **Pool-Elected Seats** — Elected by the Governance Pool at Stage 4. At Stage 3, at least one seat must be filled by Active Contributor election. At Stage 4, all seats (except the Independent Seat) are Pool-Elected.

  (c) **Independent Seat (1)** — An external advisor with relevant expertise (legal, financial, nonprofit governance) appointed by the Board. Must have no employment or financial relationship with any commercial licensee.

**B.3.2 Rolling Tenure.** Board members serve **indefinitely** — there are no fixed terms, scheduled elections, re-election cycles, or lifetime caps. A Board member's tenure continues until:

  (a) They voluntarily step down; or

  (b) They are replaced through the Continuous Confidence Vote (B.3.3).

  A Board member who has been replaced may be nominated and elected to the Board again in the future — there is no restriction on re-election. Board members retain their standing in the Governance Pool based on their C-points (B.2.5); serving on the Board does not diminish their Pool rank.

**B.3.3 Continuous Confidence Vote.** At Stage 4, each Board member has an **auto-generated, always-open confidence vote** maintained by the Organization. This vote exists from the moment a member joins the Board and remains open for the duration of their tenure. It functions as a **health bar** — Board members start at full confidence and remain until the community actively erodes it. The mechanics are:

  (a) **Auto-seed**: When a Board member takes their seat, every current Governance Pool member is automatically set to **"keep"** for that member. New members joining the Pool after that point also default to "keep" for all sitting Board members. The Board member begins at full health.

  (b) **Three states**: Every Pool member holds one of three positions for each Board member, changeable at any time:

  - **"Keep"** (default) — supports the Board member's continued service;
  - **"Neutral"** — withdraws active support without calling for replacement. Counted toward participation but does not count toward the keep/replace ratio;
  - **"Replace"** — calls for the Board member's removal. Must include a **nominee** from the Governance Pool, identified by handle or email. **Self-nomination is not permitted.**

  (c) **Activation threshold**: A Board member is replaced when **more than fifty percent (50%)** of non-neutral cast votes are "replace" AND at least **ten percent (10%)** of the Governance Pool has cast a vote (of any kind, including neutral). Neutral votes raise participation without tipping the balance — as neutrals accumulate, fewer active "replace" votes are needed to cross the 50% line among non-neutral votes.

  (d) **Replacement selection**: When the activation threshold is met, the nominee with the most "replace" votes naming them as replacement fills the seat. In the event of a tie, a **seven (7) day** ranked-choice runoff election is held among the tied candidates.

  (e) **Post-replacement reset**: After a replacement occurs, the new Board member's confidence vote resets — all Pool members are auto-seeded to "keep" for the new member (full health). The replaced member returns to the Governance Pool as a regular member and may be nominated for any future Board vacancy.

  (f) **Lapsed members**: If a Pool member loses their Active status (no merged contribution in the preceding 24 months), their vote is removed from the tally entirely. If they later regain Pool membership, they are re-seeded to "keep" for all sitting Board members.

  (g) **Transparency**: The live tally for each Board member — percentage of "keep," "neutral," and "replace," along with the current participation rate — is **publicly visible** to all Pool members at all times.

**B.3.4 Chair.** At Stages 1–3, the Board elects a Chair from among its members. At Stage 4, the Chair is selected via the same continuous confidence mechanism — Pool members may additionally indicate their preferred Chair among current Board members, and the Board member with the highest Chair preference among Pool voters serves as Chair.

**B.3.5 Vacancy.** If a seat is vacated by resignation, the Governance Pool fills it through a **fourteen (14) day** nomination and election (ranked choice, no self-nomination). If the Pool cannot fill the seat within thirty (30) days, Emergency Succession (B.7.2) applies.

### B.4 — Constraints on Board Power

**B.4.1 Published Principles.** The Board must publish and maintain a **Decision Principles Document** that articulates the criteria used for discretionary decisions (licensing approvals, Revenue Basis selection, cure period duration, Relationship Index assessment). Decisions must be consistent with these published principles. This obligation applies at all Governance Stages.

**B.4.2 Consistency Requirement.** Similar cases must be treated similarly. If a Licensee can demonstrate that its situation is materially indistinguishable from a prior case that was decided differently, it may appeal to the Board citing the inconsistency. Selective enforcement is a Charter violation.

**B.4.3 Conflict of Interest.** A Board member must recuse themselves from any decision involving a Licensee that employs them, contracts with them, or in which they hold a financial interest. Failure to recuse is grounds for recall (at stages where recall is active). No Board member may simultaneously hold a governance role in a commercial licensee.

**B.4.4 Board Accountability.** At Stage 4, every Board member is subject to the Continuous Confidence Vote (B.3.3). This always-open mechanism is the primary instrument of Board accountability — no separate recall or election process is needed. There is no lifetime cap on Board service; members who have been replaced may be re-elected to any future vacancy.

**B.4.5 Charter Amendment.** At Stages 1–3, the Charter may be amended by a **unanimous Board vote**. At Stage 4, amendment requires a vote of **two-thirds (⅔) of the Board** AND **a simple majority of Active Contributors** voting in a ratification poll conducted over no fewer than fourteen (14) days. Neither the Board alone nor Contributors alone may unilaterally change this Charter at Stage 4.

**B.4.6 Immutable Provisions.** The following provisions may not be amended or removed at ANY Governance Stage, by any process short of dissolution and reconstitution:

  (a) B.2.3 — Corporate Contribution Exclusion (corporate work ≠ governance rights);

  (b) B.2.7 — No corporate membership tiers (money cannot buy governance);

  (c) B.3.3 — Continuous Confidence Vote (Board members are always accountable to the Pool);

  (d) B.5.1 — Contributor compensation minimum (50% of fees);

  (e) B.7.3 — Dissolution asset protection (no distribution to licensees or Board);

  (f) B.9 — Governance Stage advancement triggers (Board cannot prevent stage progression);

  (g) B.9.4(e) — Stage 4 irreversibility (once ratified, contributor governance is permanent).

### B.5 — Financial Governance

**B.5.1 Contributor Compensation Floor.** No less than **fifty percent (50%)** of all fees collected under the SCCL shall be allocated to contributor compensation. This floor is immutable under B.4.6(d).

**B.5.2 Allocation Ranges.** Subject to the floor in B.5.1, the Board sets annual allocation percentages within the ranges specified in SCCL Article 4.7. Adjustments require thirty (30) days' advance notice and a published rationale.

**B.5.3 Compensation Methodology.** The Board shall publish the methodology used to determine individual contributor compensation. The methodology may consider: volume of contributions, maintenance burden assumed, security-critical work, review labor, mentorship, and community leadership. The specific formula or rubric is set by the Board and published annually.

**B.5.4 Transparency.** The Organization shall publish an annual financial report within ninety (90) days of each fiscal year end, including: (a) total fees collected, by licensee (anonymized if the Licensee requests confidentiality, but aggregate amounts must be disclosed); (b) allocation by category; (c) individual compensation amounts (with contributor consent) or anonymized distribution statistics; (d) reserve fund balance; (e) Board member compensation (if any — see B.5.5).

**B.5.5 Board Compensation.** Board members may receive reasonable compensation for their service, not to exceed the median contributor compensation in any given year. Board compensation is disclosed publicly in the annual financial report.

**B.5.6 Independent Audit.** The Organization shall engage an independent auditor to review its financial statements at least once every **three (3) years**, or annually once total fees collected exceed **$1,000,000** per year. Audit results are published.

### B.6 — Licensee Relations

**B.6.1 Relationship Index Procedures.** The Licensee Relationship Index (SCCL Definition 1.11) is maintained by the Board as an internal governance tool. The Board shall:

  (a) Document the factors considered in assessing the Index (compliance history, reporting accuracy, cure periods used, cooperative conduct, ecosystem contribution);

  (b) Review each Licensee's Index at least annually;

  (c) Not disclose a Licensee's Index score to other Licensees or the public;

  (d) Upon written request from a Licensee, provide that Licensee with a summary of its own Index standing and the factors contributing to it.

**B.6.2 License Registry.** The Organization shall maintain a public registry of: (a) all active commercial licenses (Licensee name and effective date); (b) all revocation decisions (grounds and summary, per SCCL Article 6.5c); (c) all terminations. The registry does not disclose fee amounts or Relationship Index scores.

### B.7 — Succession and Continuity

**B.7.1 No Single Point of Failure.** The Organization must ensure that no single individual's departure — whether sudden or planned — can paralyze governance. The rolling tenure model (B.3.2), Continuous Confidence Vote (B.3.3), vacancy procedures (B.3.5), and emergency succession (B.7.2) provide structural continuity.

**B.7.2 Emergency Succession.** If the Board falls below quorum (defined as a simple majority of filled seats) and cannot fill vacancies through normal procedures, the **five (5) most recent Active Contributors by contribution date** shall convene within fourteen (14) days to appoint interim Board members until a proper election can be held.

**B.7.3 Dissolution.** If the Organization dissolves, all assets (after settling liabilities) shall be transferred to a 501(c)(3) organization with a compatible mission, as selected by a majority vote of Active Contributors. The Software remains available under AGPLv3 regardless of the Organization's status. Under no circumstances may assets be distributed to any commercial licensee, Board member, or their affiliates.

### B.8 — Activation Threshold

**B.8.1** Per SCCL Article 2.1, no commercial license may be issued until the Project has at least **fifteen (15) Active Contributors** (as defined in B.2.2, excluding corporate contributions per B.2.3), of which no single legal entity (including subsidiaries and affiliates) accounts for more than **twenty-five percent (25%)** of total C-points (B.2.5) over the preceding twelve (12) months.

**B.8.2** The Board verifies the threshold at the time of each license issuance.

### B.9 — Governance Stages

The Organization's governance evolves through four stages. Stage advancement from Stage 1 through Stage 3 is triggered by **objective, measurable criteria** and is **automatic** — the Board cannot prevent or delay stage progression when the criteria are met. Stage 4 requires a **community vote** (see B.9.4). Stage regression may occur between Stages 1–3 if the criteria for the current stage are no longer met for twelve (12) consecutive months. **Stage 4 is permanent and irreversible.**

**B.9.1 Stage 1 — Founding (Autocratic)**

  *Trigger:* Default state from incorporation.

  *Governance:*
  - The Board governs with full authority.
  - No contributor elections, no recall, no community veto.
  - The Board makes all decisions regarding licensing, fees, and ecosystem management.
  - Immutable provisions (B.4.6) still apply. Published Principles (B.4.1) still apply.
  - No commercial licenses may be issued (activation threshold not met).

  *Exits when:* The project reaches **15 Active Contributors** (per B.2.2, excluding corporate contributions).

**B.9.2 Stage 2 — Growth (Consultative)**

  *Trigger:* 15+ Active Contributors.

  *Governance:*
  - The Board retains full decision-making authority.
  - The Board must **publish all licensing decisions** and their rationale.
  - The Board must conduct a **quarterly community consultation** — a public forum where Active Contributors may comment on licensee relationships, fee decisions, and ecosystem direction. The Board must publish written responses to community input within thirty (30) days.
  - Community input is **advisory and non-binding** — the Board decides.
  - SCCL commercial licenses may now be issued (activation threshold met).

  *Exits when:* The project reaches **30 Active Contributors** AND at least **one (1) active SCCL commercial license** has been issued.

**B.9.3 Stage 3 — Traction (Participatory)**

  *Trigger:* 30+ Active Contributors AND 1+ active commercial licensee.

  *Governance:*
  - **Contributor-Elected Board seats activated.** At least one (1) seat on the Board must be filled by contributor election. Additional Contributor-Elected seats are added as the Board grows (maintaining a path toward majority).
  - The Board must **formally consider and respond to** community positions on licensee relationships. If a majority of Active Contributors express a position on a licensing decision (via public poll or petition), the Board must address it in writing with specific reasons if it disagrees.
  - Active Contributors may **petition the Board** on any matter with fifteen percent (15%) of Active Contributors signing. The Board must respond within thirty (30) days.
  - The Board must publish an **annual governance report** reviewing its own performance, decisions made, and community feedback received.

  *Exits when:* The project reaches **500 Active Contributors** AND at least **three (3) active SCCL commercial licensees**, triggering Stage 4 eligibility (see B.9.4).

**B.9.4 Stage 4 — Maturity (Contributor-Governed)**

  Stage 4 represents a fundamental and **permanent** shift in governance: the Board transitions from decision-maker to executive, and the contributor community becomes the legislative body. There is no turning back from Stage 4.

  *Eligibility Trigger:* 500+ Active Contributors AND 3+ active commercial licensees.

  *Activation Process — Rolling Vote:*

  (a) When the eligibility criteria are met, the Board must publicly announce Stage 4 eligibility within **thirty (30) days** and open the **Stage 4 Rolling Vote**. If the Board fails to announce, any Active Contributor may initiate the process directly by publishing the eligibility verification and opening the vote.

  (b) The Rolling Vote is **permanent and continuous** — it does not close. Once opened, it remains open for as long as the project is at Stage 3. Active Contributors may cast, change, or withdraw their vote at any time.

  (c) Only votes from current **Active Contributors** (per B.2.2) are counted in the tally. If a Contributor's Active status lapses (no merged contribution in the preceding 24 months), their vote is automatically removed from the tally. If they later regain Active status through a new contribution, they may vote again.

  (d) The live tally is classified continuously as follows:

  - **Positive**: More than 52.5% of cast votes are in favor, AND votes have been cast by at least **fifteen percent (15%)** of current Active Contributors.
  - **Negative**: More than 52.5% of cast votes are against, AND votes have been cast by at least **fifteen percent (15%)** of current Active Contributors.
  - **Neutral**: Fewer than 15% of Active Contributors have cast votes, OR the tally falls within **five (5) percentage points of 50%** (i.e., 47.5%–52.5%).

  (e) **Positive tally** — Stage 4 activates immediately, regardless of contributor count. The community has chosen self-governance.

  (f) **Neutral tally at 1000+ Active Contributors** — Stage 4 activates automatically. At this scale, the absence of opposition is treated as consent.

  (g) **Negative tally** — The project remains at Stage 3 regardless of contributor count. The community's explicit rejection is respected — even at 1000+ contributors, Stage 4 does **not** auto-activate while the tally is Negative. Stage 4 can only activate when the live tally shifts to Positive or when it becomes Neutral at 1000+ contributors.

  (h) Once Stage 4 is activated — whether by Positive tally or Neutral auto-transition — **it cannot be reversed** by any mechanism. The community's self-governance is permanent.

  *Governance — The Governance Pool as Legislature:*

  - The **Governance Pool** (B.2.6) — the top 1000 Active Contributors by C-points (B.2.5) — becomes the legislative body of the Organization.
  - The Board's role transitions to **executive and operational**: managing day-to-day operations, licensee relationships, fee collection, compliance enforcement, financial administration, and serving as the public face of the Organization.
  - **Pool members may initiate binding votes** on any matter within the Organization's scope, including licensing policy, fee principles, ecosystem direction, Code of Conduct changes, and fund allocation within the ranges of SCCL Article 4.7.
  - The Board may also initiate votes, on equal footing with any Pool member.
  - Binding votes require a **simple majority** of participating Pool members, with a minimum participation threshold of **ten percent (10%)** of the Governance Pool to ensure legitimacy.
  - **Board Final Say (Veto).** The Board retains a veto on community resolutions. To exercise a veto, the Board must publish a **detailed written justification** within fifteen (15) days of the vote result. A vetoed resolution may be overridden by a **two-thirds (⅔) supermajority** of the Governance Pool in a subsequent override vote. If the override passes, the Board must implement the resolution.
  - **Dual-approval Charter amendments** (B.4.5) — Board ⅔ + Pool majority required.

  *Governance — Board at Stage 4:*

  - **All Board seats are Pool-Elected** (except the Independent Seat per B.3.1c). Founder Seats have expired or converted (per B.3.1a). The Board may not self-appoint members.
  - Board members serve **indefinitely** on a rolling basis (B.3.2) — no terms, no scheduled elections, no lifetime caps. They serve until replaced by the Continuous Confidence Vote (B.3.3) or voluntary resignation. Board members retain their Governance Pool standing.
  - Every Board member is subject to an **always-open Continuous Confidence Vote** (B.3.3) — Pool members start at "keep" and may shift to "neutral" or "replace" at any time. Replaced members return to the Pool and may be re-elected.
  - The Governance Pool selects the **Chair** directly (B.3.4).
  - Emergency Succession (B.7.2) remains available as a last resort if the Board falls below quorum.

**B.9.5 Stage Verification.** The Board must verify and publicly declare the current Governance Stage annually. Any Active Contributor may challenge the declared stage by demonstrating that the objective criteria for a different stage are met. Disputes are resolved by an independent count of Active Contributors as recorded in the project's version control history.
