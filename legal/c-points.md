# Project JARVIS — Contribution Points (C-points) Methodology

**Version:** 1.0-draft
**Effective date:** To be set upon Organization formation (see SCCL-v1.md Article 2.1)

> **Note:** This document is a working draft requiring Board ratification before becoming operative. See issue #83.

---

## Overview

C-points are a weighted composite score that ranks contributors for eligibility in the **Governance Pool** (top 1000 contributors by C-point score, as defined in the Organization Charter, Appendix B §B.2.5). C-points replace raw merged commit count as the ranking factor, acknowledging that not all contributions carry equal weight.

C-points are **not currency** and carry no direct monetary value. They determine Governance Pool membership, which in turn determines eligibility for the contributor compensation pool described in the Charter.

---

## 1. Variables and Weights

### 1a. Affiliation Disclosure (required, gates Pool/Board eligibility)

Every Contributor must disclose, and keep current, their present employer(s), contracting engagements, and any other relationship a reasonable person would consider a material financial tie — updated within **thirty (30) days** of any change. This is not a scored dimension; it is a compliance gate. Its purpose is to make the Employer Concentration Limit (Organization Charter §B.2.8) enforceable — that Section is a structural cap, not a trust-based rule, and a structural cap only works if affiliation is known.

- A Contributor with no disclosure on file, or a stale one (unconfirmed for 12+ months), is ineligible for Governance Pool ranking and Board candidacy until it is filed or reconfirmed. Their C-points continue to accrue for compensation purposes (§6) regardless — this gate affects governance eligibility only, never pay.
- Disclosure is self-reported but subject to Board audit (§3.5) and public challenge; a Contributor may dispute another's disclosed (or apparently undisclosed) affiliation through the same channel used for C-point disputes.
- The current disclosure record (employer/affiliation only, not compensation figures) is visible to the Board and to Organization tooling enforcing B.2.8; it is not published to the general public, to avoid inviting targeted harassment of individual contributors.

### 1b. Dimensions and Weights

The C-point score for a contributor over a rolling 24-month window is calculated as:

```
C_raw = Σ (dimension_score × weight)
C_final = C_raw × Integrity_Multiplier   (see §3.6)
```

| # | Dimension | Weight | Description |
|---|-----------|--------|-------------|
| 1 | Merged contribution volume | 20% | Number of merged pull requests, normalized by repository activity level. |
| 2 | Code scope | 20% | Complexity-adjusted lines of code changed, measured by cyclomatic complexity of modified functions. Pure formatting/whitespace changes score zero. |
| 3 | Review labor | 15% | Pull requests reviewed, weighted by review thoroughness (comment depth, identified issues resolved). |
| 4 | Security-critical work | 15% | Security patches, vulnerability fixes, security audit participation, threat-model contributions. Weighted higher due to specialized skill and project risk reduction. |
| 5 | Maintenance burden | 10% | Long-term module ownership, triage work (issue triage, milestone tracking), on-call/incident response. |
| 6 | Documentation contributions | 10% | User-facing documentation, API docs, guides, examples — excluding auto-generated docs. |
| 7 | Mentorship and community | 5% | Onboarding new contributors, answering questions in issues/discussions, reviewing contributor-submitted drafts. |
| 8 | Community leadership | 5% | Organizing, moderating, representing the project at external venues (conferences, publications). |

### Weight adjustment

Weights are set by the Board at license activation and may be adjusted via the Charter amendment process (Charter §B.4.5). Individual weights may be changed by up to ±5 percentage points per amendment cycle without triggering a full Charter revision; larger changes require the standard amendment quorum.

---

## 2. Calculation Mechanics

### 2.1 Time window

C-points are calculated over a **rolling 24-month window**, matching the Active Contributor definition. Contributions older than 24 months fall out of the window on a daily rolling basis.

Rationale: a rolling window keeps the Governance Pool populated by currently active contributors rather than rewarding historical work that may no longer reflect project involvement.

### 2.2 Recalculation frequency

C-point scores are recalculated **daily** via automated tooling (see §5). The Governance Pool membership list (top 1000) is published monthly as a snapshot.

### 2.3 Normalization

Each dimension score is normalized to a 0–100 scale before weighting, using the 95th-percentile contributor in that dimension as the normalization ceiling. This prevents any single prolific contributor from dominating the composite score through volume alone.

### 2.4 Multi-category contributions

A contribution may span multiple categories (e.g., a security fix that also includes documentation). Each category's score is credited independently; there is no double-counting penalty.

### 2.5 Reverted contributions

If a merged contribution is subsequently reverted, the C-points credited for that contribution are removed retroactively from the contributor's score at the time of revert.

---

## 3. Anti-Gaming Measures

### 3.1 Minimum quality threshold

A pull request must meet the project's code quality bar (CI passing, review approval from a maintainer) to generate any C-points. Merged-but-reverted PRs, trivial changes (e.g., single-character fixes, whitespace normalization), and automated/bot submissions are excluded.

### 3.2 Volume floor

A single contributor's **merged contribution volume** dimension (dimension 1) is capped at the 95th-percentile normalization ceiling. Submitting more than the ceiling amount of PRs in the window does not increase the score beyond the ceiling for that dimension.

### 3.3 Review credit requires engagement

Review labor (dimension 3) credit requires reviews with substantive content (minimum comment threshold set by the Board). Rubber-stamp approvals with no comments do not generate review credits.

### 3.4 Bot and automated contributions excluded

Contributions authored by automated systems, bots, or CI pipelines are ineligible for C-points regardless of merge status. Contributions must be attributed to a human contributor identity.

### 3.5 Board audit rights

The Board may audit any contributor's C-point breakdown and disqualify contributions that appear to be the result of gaming or coordinated inflation. Disputes are resolved under the Charter dispute resolution process.

### 3.6 Integrity Multiplier

`Integrity_Multiplier` starts at **1.0** for every Contributor and applies to their total score (`C_final = C_raw × Integrity_Multiplier`) — it affects both Governance Pool rank and compensation-pool share, since both are computed from `C_final`. It is reduced, proportionate to severity and never below **0.0**, on any of the following findings by the Board (subject to the same dispute/appeal process as any C-point determination):

| Finding | Typical range |
|---|---|
| Confirmed gaming or coordinated inflation (§3.5) | 0.25–0.75, scaled to the share of score affected |
| Undisclosed or stale affiliation (§1a), first instance | 0.75–0.9, for the period of non-disclosure only |
| Undisclosed affiliation, willful or repeated | 0.0–0.5, and referred to Charter B.4.3 for Pool/Board disqualification |
| Code of Conduct violation resulting in formal Board sanction | Set case-by-case by the Board, published with rationale per B.4.1 |

A reduced multiplier applies only to the window it covers — it is not a permanent mark. A Contributor may appeal a multiplier determination through the same channel as any other C-point dispute (§3.5), and the Board must publish its reasoning per Charter B.4.1/B.4.2 (consistency across similar cases).

---

## 4. Edge Cases

| Scenario | Treatment |
|----------|-----------|
| Contributor leaves and contributions are reverted en masse | C-points removed retroactively for reverted work; remaining valid work retains credits. |
| Co-authored contribution (two contributors) | C-points split equally between co-authors unless otherwise noted in the PR. |
| Contribution submitted under CLA (commercial) | C-points not awarded for contributions made under the CLA (commercial obligation). Only voluntary community contributions earn C-points. |
| Contributor changes GitHub identity | Tooling must map old → new identity; historical C-points transfer. Contributor must notify the Organization to trigger identity merge. |

---

## 5. Tooling

As required by Charter §D.3, contributors must be able to view their C-point score and per-dimension breakdown.

**Planned interfaces:**
- **Web dashboard** — available at a URL published in the Organization's public registry. Shows total score, 24-month trend, and per-dimension breakdown.
- **CLI command** — `jarvis contrib score [--contributor <github-handle>]` returns the score for the caller or a named contributor.
- **GitHub Action** — runs on PR merge to update the contributor's score in the registry.

The canonical score is stored in the Organization's registry (not derived from GitHub stats alone), to allow for Board adjustments, identity merges, and audit overrides.

---

## 6. Governance Pool Membership

The **Governance Pool** consists of the top 1000 contributors by `C_final` score in the rolling 24-month window, subject to the **Employer Concentration Limit** (Organization Charter §B.2.8): no single Employer Group (§1a) may exceed 20% of the 1000 seats, and eligibility additionally requires a current Affiliation Disclosure (§1a). Membership is recalculated monthly.

Governance Pool members receive:
- A weighted vote on Charter amendments and major project decisions (Charter §B.3).
- Eligibility for the contributor compensation pool distribution (Charter §B.5).
- Recognition in the public contributor registry.

A contributor who leaves the top 1000 loses Governance Pool membership at the next monthly recalculation. There is no grandfather provision.

---

*This methodology document is maintained by the Organization Board. Amendment proposals should be submitted as pull requests to this file with a corresponding Charter amendment reference.*
