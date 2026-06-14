# Sovereign Commons Commercial License (SCCL)

## Version 1.0 — DRAFT

> **Status:** This license is a working draft and has not been reviewed by legal counsel. It is published for transparency, community feedback, and iteration. Do not rely on this document as a binding legal instrument until it is finalized.

---

## Preamble

This license exists because the commons should not be exploited without accountability. Free and open-source software is built by communities, for communities. When commercial entities benefit from that labor, the Organization that stewards the software retains the right to demand alignment with the values that built it.

This License also exists because **unpaid maintainers are a security vulnerability.** Critical open-source infrastructure — software that underpins the internet, financial systems, and national security — is routinely maintained by unpaid volunteers working alone, without resources for security audits, code review, or sustainable development practices. The 2024 XZ Utils backdoor (CVE-2024-3094) demonstrated the consequences: a state-level actor spent years social-engineering a burned-out solo maintainer into handing over commit access, nearly compromising SSH authentication on every Linux server worldwide. The attack did not exploit a technical weakness — it exploited the fact that a critical piece of global infrastructure had no funding, no support structure, and a single point of human failure. This License addresses that structural weakness by making contributor compensation mandatory, not optional. Funded maintainers are resilient maintainers. A sustainable commons is a secure commons.

This License offers commercial entities an alternative to the GNU Affero General Public License, Version 3 ("AGPLv3"), under which the Software is also made available. Entities that do not wish to comply with AGPLv3's source disclosure requirements may instead operate under this License, subject to the conditions herein.

The defining feature of this License is **Board-governed commercial access**: the Organization's Governing Board retains unilateral authority to grant, condition, and revoke commercial licenses, ensuring that every commercial use of the Software remains aligned with the Project's values, ethics, and ecosystem.

---

## Article 1 — Definitions

**1.1 "Organization"** means the legal entity (foundation, non-profit, LLC, or equivalent) that owns or stewards the Software and its ecosystem. The Organization acts as Licensor under this Agreement.

**1.2 "Governing Board"** (or "Board") means the decision-making body of the Organization, responsible for issuing, conditioning, and revoking commercial licenses under this Agreement. The Board's composition and procedures are defined in the Organization's charter or bylaws.

**1.3 "Licensee"** means the legal entity (company, organization, or individual acting in a commercial capacity) entering into this Agreement with the Organization.

**1.4 "Software"** means the original work of authorship made available under this License, including all source code, object code, documentation, and associated files, as identified at the time of licensing.

**1.5 "Derivative Work"** means any work that is based on, incorporates, modifies, or is derived from the Software, whether in whole or in part.

**1.6 "Contribution"** means any modification, improvement, addition, or Derivative Work submitted by the Licensee for inclusion in the Software.

**1.7 "Ecosystem Alignment"** means the Licensee's ongoing adherence to the behavioral and ethical standards set out in Article 3 of this License.

**1.8 "Annual License Fee"** means the fee payable by the Licensee each year, calculated in accordance with the Fee Formula (Appendix A).

**1.9 "Effective Date"** means the date on which the Licensee executes this Agreement or first exercises rights granted hereunder, whichever is earlier.

**1.10 "Cure Period"** means the period granted to a Licensee to remedy a breach or misalignment before further action is taken, as determined by the Board in accordance with Article 6.3.

**1.11 "Licensee Relationship Index"** means the Board's internal, qualitative assessment of a Licensee's cumulative conduct, compliance history, and relationship with the Organization and its ecosystem. The Index is maintained by the Board as a governance instrument and may inform the Board's exercise of discretion under this License, including but not limited to licensing decisions, Cure Period duration, Revenue Basis, and reinstatement applications.

**1.12 "Revenue Basis"** means the financial figure used to calculate the Annual License Fee under Appendix A. The Revenue Basis is determined by the Board for each Licensee based on the Licensee's Relationship Index, and falls within the following spectrum, ordered by severity of the Board's stance:

  (a) **Profit on the Software** — net profit attributable to products or services incorporating the Software, after directly attributable expenses (most lenient);

  (b) **Profit on the Company** — the Licensee's total net income across all business lines;

  (c) **Profit on the Ecosystem** — combined net income of the Licensee and all affiliated entities (subsidiaries, parent companies, and entities under common control);

  (d) **Revenue on the Software** — gross revenue attributable to products or services incorporating the Software;

  (e) **Revenue on the Company** — the Licensee's total annual gross revenue across all business lines;

  (f) **Revenue on the Ecosystem** — combined total gross revenue of the Licensee and all affiliated entities (most restrictive).

**Note:** The tiers above are ordered by the severity of the Board's intent, not by guaranteed monetary value. In practice, a narrower-scope revenue figure (e.g., Revenue on the Software) may be smaller than a broader-scope profit figure (e.g., Profit on the Ecosystem) depending on the Licensee's business structure. The Board should consider the actual financial impact when selecting a tier, ensuring the chosen basis reflects the appropriate level of accountability rather than simply maximizing the fee.

"Affiliated entities" means any entity that directly or indirectly controls, is controlled by, or is under common control with the Licensee, where "control" means ownership of more than fifty percent (50%) of the voting securities or equivalent ownership interest.

The Board shall communicate the applicable Revenue Basis to the Licensee in writing at the time of license issuance and at each annual renewal.

---

## Article 2 — Grant of Commercial License

**2.1 Activation Threshold.** This License may not be issued to any Licensee until the Project has at least **fifteen (15) Active Contributors** (as defined in Appendix B, Section B.2.2, excluding corporate contributions per B.2.3), of which no single legal entity (including its subsidiaries and affiliates) accounts for more than **twenty-five percent (25%)** of total contributions measured by commit authorship over the preceding twelve (12) months. This threshold is verified by the Board at the time of license issuance.

**2.2 Board Approval.** Commercial licenses are granted at the sole discretion of the Governing Board. The Board may accept, reject, or condition any application for a commercial license, and is not required to provide reasons for rejection.

**2.3 Grant.** Subject to the Licensee's continuous compliance with all terms of this License, the Organization hereby grants the Licensee a limited, non-exclusive, non-transferable, non-sublicensable license to:

  (a) Use, copy, and internally deploy the Software for commercial purposes;

  (b) Incorporate the Software into proprietary products and services without the source disclosure obligations imposed by AGPLv3;

  (c) Distribute the Software as part of a commercial product or service, provided all conditions of this License are met.

**2.4 Limitations.** This License does not grant the Licensee the right to:

  (a) Sublicense or transfer rights to third parties;

  (b) Remove, alter, or obscure any copyright, attribution, or licensing notices in the Software;

  (c) Use the Software in any application explicitly prohibited under Article 3.4;

  (d) Claim ownership of the Software or misrepresent its origin.

**2.5 Relationship to AGPLv3.** This License is an alternative to AGPLv3, not a supplement to it. The Licensee operates under either AGPLv3 or this License, not both simultaneously. Upon termination of this License for any reason, the Licensee must either comply with AGPLv3 or cease use of the Software in accordance with Article 7.

---

## Article 3 — Ecosystem Alignment Obligations

**3.1 Code of Conduct.** The Licensee must adhere to the Project's published Code of Conduct at all times during the term of this License. The current Code of Conduct is published at [Project URL] and may be updated by the Organization with thirty (30) days' written notice to the Licensee.

**3.2 Cooperative Conduct.** The Licensee shall:

  (a) Cooperate in good faith with the Organization and its contributors on matters affecting the Software, including security disclosures, compatibility, and integration concerns;

  (b) Respond to reasonable communications from the Organization within fifteen (15) business days;

  (c) Maintain accurate, current contact information registered with the Organization for governance and compliance purposes;

  (d) Publicly attribute the Software in any product or service incorporating it, using the form: *"Built with [Software Name] — [Project URL]"*, or an equivalent form approved in writing by the Organization.

**3.3 Non-Aggression.** The Licensee shall not:

  (a) Initiate or financially support patent, copyright, or trade secret litigation against the Organization, any contributor to the Software, or any other Licensee under this License, where such litigation relates to the Software;

  (b) Engage in competitive actions specifically designed to harm the Project, including but not limited to: funding hostile forks, filing malicious DMCA notices, or coordinating efforts to undermine the Project's community or reputation;

  (c) Misrepresent the nature, origin, or capabilities of the Software in public communications, marketing, or regulatory filings.

**3.4 Prohibited Uses.** Regardless of all other terms, the Licensee shall not deploy or incorporate the Software in systems or applications primarily designed to:

  (a) Conduct mass surveillance of individuals without their explicit, informed consent;

  (b) Develop or operate autonomous weapons systems;

  (c) Engage in the unlawful collection, aggregation, or sale of personal data;

  (d) Suppress, censor, or manipulate political speech or democratic processes;

  (e) Discriminate against individuals on the basis of protected characteristics in violation of applicable law.

---

## Article 4 — Annual License Fee

**4.1 Fee Obligation.** The Licensee shall pay the Annual License Fee to the Organization on or before the anniversary of the Effective Date each year.

**4.2 Fee Calculation.** The Annual License Fee is calculated in accordance with the Fee Formula set out in Appendix A to this License, using the Revenue Basis determined by the Board for the Licensee (Definition 1.12). The Organization may update the Fee Formula with ninety (90) days' written notice, provided that no increase shall take effect mid-term of any paid year. Changes to the Licensee's Revenue Basis take effect at the next annual renewal.

**4.3 Payment Process.** The Organization shall issue an invoice or fee statement no fewer than thirty (30) days before each payment is due. Payment terms and accepted methods are as specified in the invoice.

**4.4 Self-Reporting.** The Licensee shall provide to the Organization, on an annual basis, an honest declaration of the figures required to calculate the Fee (including the applicable Revenue Basis figure, ecosystem inclusion factors, and any other data the Board reasonably requires), signed by an authorized officer of the Licensee.

**4.5 Audit Right.** The Organization reserves the right to audit the Licensee's records relevant to Fee calculation, upon thirty (30) days' written notice, no more than once per year. The cost of the audit is borne by the Organization unless the audit reveals an underpayment exceeding the **Audit Tolerance Threshold**, in which case the Licensee bears the audit cost. The Audit Tolerance Threshold is set by the Board within a range of **ten percent (10%) to fifteen percent (15%)**, and may be adjusted per Licensee based on the Licensee's Relationship Index and prior reporting accuracy.

**4.6 Non-Payment.** Failure to pay the Annual License Fee by the due date constitutes a breach. The Organization shall provide written notice of non-payment, after which the Licensee has thirty (30) days to cure. Failure to cure results in automatic suspension of this License pending payment. Suspension exceeding sixty (60) days results in termination under Article 7.

**4.7 Fund Allocation.** All fees collected under this License shall be allocated transparently in accordance with the compensation structure defined in the Organization's charter. The recommended allocation is:

  (a) **50–60%** to contributor compensation;

  (b) **15–20%** to project infrastructure (hosting, CI/CD, security audits, tooling);

  (c) **10–15%** to a reserve fund (legal defense, sustainability buffer);

  (d) **5–10%** to an emergency fund (incident response, operational contingencies);

  (e) **Up to 5%** to Organization operations and administration.

  The exact percentages are set by the Governing Board and published publicly. Adjustments require thirty (30) days' advance notice.

---

## Article 5 — Contribution Back Requirement

**5.1 Mandatory Contribution.** Any Contribution developed by or on behalf of the Licensee — including modifications, patches, improvements, or Derivative Works — must be submitted to the Organization within ninety (90) days of internal deployment or use, whichever is earlier.

**5.2 Submission Method.** Contributions shall be submitted in accordance with the Project's Contributor License Agreement (CLA) for commercial licensees, or via Developer Certificate of Origin (DCO) where specified by the Organization. The applicable method and terms are published at [CLA/DCO URL].

**5.3 Organization's Rights Over Contributions.** The Organization may, at its sole discretion:

  (a) Merge the Contribution into the publicly available Software;

  (b) Hold the Contribution privately without public disclosure;

  (c) Decline the Contribution, in which case the Licensee retains ownership of the Contribution but remains bound by the contribution obligations of this Article for future Contributions.

**5.4 No Forced Disclosure of Business Logic.** The Contribution obligation applies to changes made to the Software itself, not to the Licensee's proprietary business logic, data, or configurations that are separate from and do not modify the Software's codebase.

---

## Article 6 — License Revocation

**6.1 Board Authority.** The Governing Board retains unilateral authority to revoke any commercial license granted under this Agreement. Revocation is a measure of last resort, exercised when a Licensee has materially violated the terms or spirit of this License and lesser remedies have proven insufficient.

**6.2 Grounds for Revocation.** The Board may initiate revocation proceedings on the basis of any of the following:

  (a) Material breach of Ecosystem Alignment obligations (Article 3) that has not been cured within the applicable Cure Period;

  (b) Persistent pattern of conduct materially harmful to the Project, its contributors, or its users;

  (c) Conduct constituting a Prohibited Use under Article 3.4;

  (d) Material misrepresentation in Fee self-reporting under Article 4.4;

  (e) Bad-faith conduct in any dispute with the Organization or its contributors.

**6.3 Due Process.**

  (a) Before revoking a license, the Board shall notify the Licensee in writing of the specific grounds for the proposed revocation, including documentary evidence where available.

  (b) The Board shall grant the Licensee a Cure Period whose duration is determined by the Board at its discretion, taking into account the severity of the breach, the Licensee's Relationship Index, and any prior compliance history. The Cure Period shall be no fewer than **fifteen (15) days**.

  (c) The Licensee shall be permitted to submit a written response to the Board within the Cure Period.

  (d) If the Licensee remedies the conduct to the satisfaction of the Board within the Cure Period, the revocation proceedings are dismissed and the Licensee enters a **twelve (12) month Probationary Period**.

**6.4 Probation and Escalation.**

  (a) During a Probationary Period, if the Board identifies a new breach (same or different grounds), the Board may set a shorter Cure Period, subject to the fifteen (15) day minimum.

  (b) If the Licensee requires a third cure within any rolling **twenty-four (24) month** window, the Board may revoke the license immediately with no Cure Period.

  (c) Where the Board determines that a Licensee has engaged in a pattern of repeated violations followed by tactical remediation designed to exploit the Cure Period, the Board may waive the Cure Period entirely.

**6.5 Revocation Decision.**

  (a) If the Cure Period expires without satisfactory remedy, or if the Board exercises immediate revocation under Article 6.4(b) or 6.4(c), the Board shall issue a formal Revocation Notice to the Licensee within fifteen (15) business days of the decision.

  (b) The Revocation Notice shall state the grounds, summarize the evidence, and reference the Licensee's response (if any).

  (c) All revocation decisions and their stated grounds shall be published in the Organization's public license registry for transparency and accountability.

**6.6 Effect of Revocation.** Upon receipt of a Revocation Notice:

  (a) This License is terminated effective ninety (90) days from the date of the Revocation Notice (the "Transition Period");

  (b) During the Transition Period, the Licensee may continue operating under this License on existing deployments only, with no new deployments;

  (c) Before the Transition Period expires, the Licensee must either: (i) come into full compliance with AGPLv3; or (ii) remove the Software from all its systems;

  (d) Failure to comply by the end of the Transition Period exposes the Licensee to enforcement under AGPLv3 and applicable copyright law, including claims for damages for the period of unauthorized commercial use.

**6.7 No Retroactive Fee Refund.** Revocation does not entitle the Licensee to a refund of any fees paid.

---

## Article 7 — Termination

**7.1 Termination for Breach.** This License terminates automatically upon:

  (a) Failure to cure non-payment within the period specified in Article 4.6;

  (b) Material breach of any term of this License not cured within the Cure Period set by the Board in accordance with Article 6.3;

  (c) A revocation decision by the Board under Article 6.

**7.2 Effect of Termination.** Upon termination:

  (a) All rights granted under this License immediately cease (subject to the Transition Period in Article 6.6 where applicable);

  (b) The Licensee must, within ninety (90) days: comply with AGPLv3 for any continued use, or remove all copies of the Software from its systems;

  (c) Obligations that by their nature survive termination (including Article 5 for Contributions already developed, Article 6.7, and this Article 7.2) shall survive.

**7.3 Reinstatement.** A terminated Licensee may apply for a new license at the Board's sole discretion. The Board may consider the Licensee's Relationship Index, prior compliance history, and the circumstances of termination in evaluating any reinstatement application.

---

## Article 8 — General Provisions

**8.1 No Warranty.** THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, OR NON-INFRINGEMENT.

**8.2 Limitation of Liability.** TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL THE ORGANIZATION OR CONTRIBUTORS BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES ARISING OUT OF OR IN CONNECTION WITH THIS LICENSE OR THE SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. THE ORGANIZATION'S AGGREGATE LIABILITY SHALL NOT EXCEED THE TOTAL FEES PAID BY THE LICENSEE IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM.

**8.3 Governing Law and Jurisdiction.** This License shall be governed by and construed in accordance with the laws of **[JURISDICTION TO BE SPECIFIED]**. Any disputes shall be subject to the exclusive jurisdiction of the courts of **[JURISDICTION]**.

**8.4 Entire Agreement.** This License, together with its Appendices, constitutes the entire agreement between the parties regarding commercial use of the Software and supersedes all prior negotiations, representations, or agreements.

**8.5 Severability.** If any provision of this License is found invalid or unenforceable, it shall be modified to the minimum extent necessary to make it enforceable. The remaining provisions shall continue in full force.

**8.6 No Waiver.** Failure by the Organization to enforce any provision of this License shall not constitute a waiver of the right to enforce it in the future.

**8.7 Amendments.** The Organization may publish revised versions of this License. Licensees operating under a specific version are not automatically subject to a new version; continued use after the transition date specified in any revision notice constitutes acceptance.

**8.8 Notices.** All notices under this License shall be in writing and delivered to the registered contact addresses of each party.

---

## Appendix A — Fee Formula

> ### Formula
>
> ```
> Annual Fee = revenue_basis × K₁ × (1 + K₂ × inclusion_index) × inflation_factor
> ```
>
> Where:
> - `revenue_basis` — Determined by the Board per Licensee based on the Licensee's Relationship Index (see Definition 1.12). Six tiers from Profit on the Software (most lenient) to Revenue on the Ecosystem (most restrictive). Tiers are ordered by severity of intent, not guaranteed monetary value — see the note in Definition 1.12.
> - `K₁` — rate constant (percentage of revenue basis)
> - `K₂` — ecosystem lock-in penalty multiplier
> - `inclusion_index` — 0.0 (fully open) to 1.0 (fully locked-in), assessed by the Board
> - `inflation_factor` — `(1 + CPI_change + buffer%)`, adjusted annually
>
> Constants and assessment methodology to be finalized.
>
> ### Dynamic Nature of the Fee
>
> The Annual License Fee is **hyper-dynamic by design**. Multiple variables in the formula — including the Revenue Basis, inclusion index, and applicable constants — are subject to Board discretion and may change between annual terms based on the Licensee's conduct, Relationship Index, and ecosystem alignment.
>
> The Licensee acknowledges that the Fee is not a fixed or purely formulaic calculation, but a Board-governed assessment informed by the formula above and the Licensee's overall relationship with the Organization.
>
> ### Negotiation
>
> If the Licensee considers the Fee proposed by the Board to be unreasonable or misaligned with the Licensee's actual use of the Software, the Licensee may request a negotiation with the Board before the annual payment is due. The Board shall consider such requests in good faith but is not obligated to adjust the Fee. If no agreement is reached, the Licensee may:
>
>   (a) Pay the Fee as proposed by the Board to maintain the commercial license;
>
>   (b) Decline the Fee and transition to AGPLv3 compliance in accordance with Article 2.5; or
>
>   (c) Cease use of the Software in accordance with Article 7.
>
> **Current status:** Constants and assessment methodology under development. See project governance discussions.

---

## Appendix B — Organization Charter

> **Status:** Working draft. Subject to legal review and community feedback.

### B.1 — Purpose and Identity

**B.1.1** The Organization exists to steward the Software and its ecosystem for the benefit of contributors and users, not corporations or investors. It is a **non-profit foundation** structured to resist capture by any single entity, corporate or individual.

**B.1.2** The Organization's governance evolves with its community. In its early stages, the Board governs with full authority — because a nascent project cannot afford governance paralysis. As the community grows, contributors earn progressively more governance power, until the Board serves at the community's pleasure. This progression is formalized in B.9 (Governance Stages).

**B.1.3** The Organization shall be incorporated as a **501(c)(3) public charity** or equivalent non-profit legal structure in the applicable jurisdiction. The 501(c)(3) structure ensures: (a) donations are tax-deductible for individual supporters; (b) the Organization cannot primarily serve corporate interests; (c) no individual or entity may profit from the Organization's dissolution.

### B.2 — Membership

**B.2.1 Contributors.** Any individual who has authored at least one merged contribution to the Software (as evidenced by the project's version control history and signed off via DCO) is a Contributor. Contributors are the primary stakeholders of the Organization.

**B.2.2 Active Contributors.** A Contributor is "Active" if they have authored at least one merged contribution within the preceding twenty-four (24) months. Only Active Contributors may exercise governance rights available at the current Governance Stage.

**B.2.3 Corporate Contribution Exclusion.** Contributions submitted on behalf of a commercial licensee — including those fulfilling the contribution-back obligation under SCCL Article 5, signed under the CLA, or authored in the scope of employment with a licensee — do **not** count toward an individual's Active Contributor status for governance purposes. Such contributions satisfy the licensee's contractual obligations but confer no governance rights.

**B.2.4 Individual Contributions by Licensee Employees.** An individual employed by a commercial licensee may qualify as an Active Contributor if and only if: (a) the contribution is signed off via DCO (not CLA); (b) the contribution is made outside the scope of their employment; and (c) the contribution is not fulfilling any part of their employer's SCCL Article 5 obligations. The individual's governance rights are personal and may not be directed or influenced by their employer.

**B.2.5 No Corporate Membership Tiers.** The Organization does not sell board seats, governance influence, or voting rights. Commercial licensees fund the ecosystem through the Annual License Fee (Article 4); this entitles them to use the Software commercially — not to govern it.

### B.3 — Governing Board

**B.3.1 Composition.** The Board shall consist of **five (5) to nine (9) members**, with the exact number set by Board resolution. The Board is composed as follows:

  (a) **Founder Seats (up to 2)** — Appointed by the founding members of the Organization. Founder seats convert to Contributor-Elected seats when the founder steps down or after a maximum of **ten (10) years** from the Organization's incorporation, whichever comes first.

  (b) **Contributor-Elected Seats** — Elected by Active Contributors. Activated at Governance Stage 3 (see B.9). At Stage 4, Contributor-Elected seats must constitute a **strict majority** of the Board (more than 50%).

  (c) **Independent Seat (1)** — An external advisor with relevant expertise (legal, financial, nonprofit governance) appointed by the Board. Must have no employment or financial relationship with any commercial licensee.

**B.3.2 Terms.** Board members serve **three (3) year terms**, staggered so that no more than one-third of seats are up for election in any given year. No individual may serve more than **two (2) consecutive terms**. After a mandatory one-term gap, a former member may stand for re-election.

**B.3.3 Elections.** Once Contributor-Elected seats are activated (Stage 3+), they are filled by ranked-choice vote among Active Contributors. Each Active Contributor has one vote per seat. Candidates must be Active Contributors themselves. Self-nominations are permitted. Elections are conducted annually within thirty (30) days of the Board's anniversary.

**B.3.4 Chair.** The Board elects a Chair from among its members. The Chair serves a **two (2) year term** as Chair. No individual may serve as Chair for more than **four (4) consecutive years**.

**B.3.5 Vacancy.** If a Contributor-Elected seat is vacated mid-term, the Board appoints an interim replacement from among Active Contributors. The interim member serves until the next regular election.

### B.4 — Constraints on Board Power

**B.4.1 Published Principles.** The Board must publish and maintain a **Decision Principles Document** that articulates the criteria used for discretionary decisions (licensing approvals, Revenue Basis selection, cure period duration, Relationship Index assessment). Decisions must be consistent with these published principles. This obligation applies at all Governance Stages.

**B.4.2 Consistency Requirement.** Similar cases must be treated similarly. If a Licensee can demonstrate that its situation is materially indistinguishable from a prior case that was decided differently, it may appeal to the Board citing the inconsistency. Selective enforcement is a Charter violation.

**B.4.3 Conflict of Interest.** A Board member must recuse themselves from any decision involving a Licensee that employs them, contracts with them, or in which they hold a financial interest. Failure to recuse is grounds for recall (at stages where recall is active). No Board member may simultaneously hold a governance role in a commercial licensee.

**B.4.4 Contributor Recall.** Activated at **Governance Stage 4 only.** If **twenty-five percent (25%)** of Active Contributors petition for the recall of a Board member (citing specific grounds), a recall vote is triggered. The recall passes with a **simple majority** of participating Active Contributors. The recalled member is immediately removed and the seat is treated as a vacancy under B.3.5.

**B.4.5 Anti-Entrenchment.** No single individual may occupy a Board seat (including non-consecutive terms) for more than **twelve (12) years** total. This lifetime cap prevents institutional knowledge from becoming institutional power. Applies at all Governance Stages.

**B.4.6 Charter Amendment.** At Stages 1–3, the Charter may be amended by a **unanimous Board vote**. At Stage 4, amendment requires a vote of **two-thirds (⅔) of the Board** AND **a simple majority of Active Contributors** voting in a ratification poll conducted over no fewer than fourteen (14) days. Neither the Board alone nor Contributors alone may unilaterally change this Charter at Stage 4.

**B.4.7 Immutable Provisions.** The following provisions may not be amended or removed at ANY Governance Stage, by any process short of dissolution and reconstitution:

  (a) B.2.3 — Corporate Contribution Exclusion (corporate work ≠ governance rights);

  (b) B.2.5 — No corporate membership tiers (money cannot buy governance);

  (c) B.5.1 — Contributor compensation minimum (50% of fees);

  (d) B.7.3 — Dissolution asset protection (no distribution to licensees or Board);

  (e) B.9 — Governance Stage advancement triggers (Board cannot prevent stage progression).

### B.5 — Financial Governance

**B.5.1 Contributor Compensation Floor.** No less than **fifty percent (50%)** of all fees collected under the SCCL shall be allocated to contributor compensation. This floor is immutable under B.4.7(c).

**B.5.2 Allocation Ranges.** Subject to the floor in B.5.1, the Board sets annual allocation percentages within the ranges specified in SCCL Article 4.7. Adjustments require thirty (30) days' advance notice and a published rationale.

**B.5.3 Compensation Methodology.** The Board shall publish the methodology used to determine individual contributor compensation. The methodology may consider: volume of contributions, maintenance burden assumed, security-critical work, review labor, mentorship, and community leadership. The specific formula or rubric is set by the Board and published annually.

**B.5.4 Transparency.** The Organization shall publish an annual financial report within ninety (90) days of each fiscal year end, including: (a) total fees collected, by licensee (anonymized if the Licensee requests confidentiality, but aggregate amounts must be disclosed); (b) allocation by category; (c) individual compensation amounts (with contributor consent) or anonymized distribution statistics; (d) reserve fund balance; (e) Board member compensation (if any — see B.5.5).

**B.5.5 Board Compensation.** Board members may receive reasonable compensation for their service, not to exceed the median contributor compensation in any given year. Board compensation is disclosed publicly in the annual financial report.

**B.5.6 Independent Audit.** The Organization shall engage an independent auditor to review its financial statements at least once every **three (3) years**, or annually once total fees collected exceed **$1,000,000** per year. Audit results are published.

### B.6 — Licensee Relations

**B.6.1 Relationship Index Procedures.** The Licensee Relationship Index (Definition 1.11) is maintained by the Board as an internal governance tool. The Board shall:

  (a) Document the factors considered in assessing the Index (compliance history, reporting accuracy, cure periods used, cooperative conduct, ecosystem contribution);

  (b) Review each Licensee's Index at least annually;

  (c) Not disclose a Licensee's Index score to other Licensees or the public;

  (d) Upon written request from a Licensee, provide that Licensee with a summary of its own Index standing and the factors contributing to it.

**B.6.2 License Registry.** The Organization shall maintain a public registry of: (a) all active commercial licenses (Licensee name and effective date); (b) all revocation decisions (grounds and summary, per SCCL Article 6.5c); (c) all terminations. The registry does not disclose fee amounts or Relationship Index scores.

### B.7 — Succession and Continuity

**B.7.1 No Single Point of Failure.** The Organization must ensure that no single individual's departure — whether sudden or planned — can paralyze governance. The staggered terms (B.3.2), vacancy procedures (B.3.5), and emergency succession (B.7.2) provide structural continuity.

**B.7.2 Emergency Succession.** If the Board falls below quorum (defined as a simple majority of filled seats) and cannot fill vacancies through normal procedures, the **five (5) most recent Active Contributors by contribution date** shall convene within fourteen (14) days to appoint interim Board members until a proper election can be held.

**B.7.3 Dissolution.** If the Organization dissolves, all assets (after settling liabilities) shall be transferred to a 501(c)(3) organization with a compatible mission, as selected by a majority vote of Active Contributors. The Software remains available under AGPLv3 regardless of the Organization's status. Under no circumstances may assets be distributed to any commercial licensee, Board member, or their affiliates.

### B.8 — Activation Threshold

**B.8.1** Per SCCL Article 2.1, no commercial license may be issued until the Project has at least **fifteen (15) Active Contributors** (as defined in B.2.2, excluding corporate contributions per B.2.3), of which no single legal entity (including subsidiaries and affiliates) accounts for more than **twenty-five percent (25%)** of total contributions measured by commit authorship over the preceding twelve (12) months.

**B.8.2** The Board verifies the threshold at the time of each license issuance.

### B.9 — Governance Stages

The Organization's governance evolves through four stages. Stage advancement is triggered by **objective, measurable criteria** and is **automatic** — the Board cannot prevent or delay stage progression when the criteria are met. Stage regression occurs if the criteria for the current stage are no longer met for twelve (12) consecutive months.

**B.9.1 Stage 1 — Founding (Autocratic)**

  *Trigger:* Default state from incorporation.

  *Governance:*
  - The Board governs with full authority.
  - No contributor elections, no recall, no community veto.
  - The Board makes all decisions regarding licensing, fees, and ecosystem management.
  - Immutable provisions (B.4.7) still apply. Published Principles (B.4.1) still apply.
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

  *Exits when:* The project reaches **100 Active Contributors** AND at least **three (3) active SCCL commercial licensees**.

**B.9.4 Stage 4 — Maturity (Contributor-Governed)**

  *Trigger:* 100+ Active Contributors AND 3+ active commercial licensees.

  *Governance:*
  - **Contributor-Elected seats become a strict Board majority** (more than 50%).
  - **Contributor Recall activated** (B.4.4) — 25% petition triggers a recall vote.
  - **Dual-approval Charter amendments** (B.4.6) — Board ⅔ + contributor majority required.
  - The Board may not override a **supermajority (⅔) community resolution** on any matter within the Organization's scope without publishing a detailed written justification and surviving a subsequent no-confidence vote (simple majority of Active Contributors). If the no-confidence vote passes, the Board must comply with the community resolution.
  - Active Contributors elect the Board Chair directly (rather than Board self-selection).

  *Stage 4 cannot regress* below Stage 3 once it has been maintained for twenty-four (24) consecutive months. This prevents a temporary dip in contributor count from stripping established governance rights.

**B.9.5 Stage Verification.** The Board must verify and publicly declare the current Governance Stage annually. Any Active Contributor may challenge the declared stage by demonstrating that the objective criteria for a different stage are met. Disputes are resolved by an independent count of Active Contributors as recorded in the project's version control history.

---

## Appendix C — Contributor Agreement Reference

> *Commercial licensees contributing back under Article 5 must sign the Project's Contributor License Agreement (CLA). Community contributors may use the Developer Certificate of Origin (DCO) — a `Signed-off-by` line in each commit.*
>
> **Current CLA:** [TO BE PUBLISHED]
>
> **DCO reference:** [developercertificate.org](https://developercertificate.org/)

---

*End of Sovereign Commons Commercial License v1.0 — DRAFT*
