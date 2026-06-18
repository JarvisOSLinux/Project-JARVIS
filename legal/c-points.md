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

The C-point score for a contributor over a rolling 24-month window is calculated as:

```
C = Σ (dimension_score × weight)
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

The **Governance Pool** consists of the top 1000 contributors by C-point score in the rolling 24-month window. Membership is recalculated monthly.

Governance Pool members receive:
- A weighted vote on Charter amendments and major project decisions (Charter §B.3).
- Eligibility for the contributor compensation pool distribution (Charter §B.5).
- Recognition in the public contributor registry.

A contributor who leaves the top 1000 loses Governance Pool membership at the next monthly recalculation. There is no grandfather provision.

---

## 7. Monthly Compensation Calculation

### 7.1 Two-Pool Structure

Contributor compensation (SCCL Article 4.7) is split into two pools, both funded annually and paid out monthly:

| Pool | Source | Distribution mechanism |
|---|---|---|
| **Base pool** (50–55% of annual fees) | Organic C-points activity | Proportional to each contributor's monthly C-points |
| **Incentive pool** (5% of annual fees) | Board-directed bounties | Allocated across subsystems/tasks; distributed to contributors who worked in those areas |

Both pools are divided by 12 to produce a monthly budget. No annual surplus rolls over — unspent incentive budget in a given month returns to the reserve fund.

### 7.2 Base Pool Distribution

Each month:

```
monthly_base_budget  = (annual_fees × base_pool_%) / 12
contributor_share    = contributor_monthly_cpoints / total_monthly_cpoints_all_contributors
contributor_payout   = contributor_share × monthly_base_budget
```

No subsystem weighting is applied to the base pool. Contributors working on less-trafficked subsystems naturally receive a larger per-contributor share because fewer people compete for the same activity.

### 7.3 Incentive Pool Distribution

Each January, the Board publishes the **annual incentive allocation** — the percentage of the monthly incentive budget directed to each subsystem or task category. These percentages reflect strategic priorities (e.g., security-critical work, underserved subsystems, documentation gaps) and may be adjusted mid-year through the Charter amendment process (Charter §B.4.5).

Example annual incentive allocation:

| Subsystem / Task | Incentive share |
|---|---|
| Security and vulnerability work | 35% |
| Core dispatch engine | 25% |
| Documentation | 20% |
| TUI | 10% |
| Voice / STT | 10% |

Each month, a contributor who worked in an incentivized area receives an additional payout proportional to their C-points within that area relative to all contributors in that area that month:

```
monthly_incentive_budget        = (annual_fees × 5%) / 12
subsystem_incentive_budget      = monthly_incentive_budget × subsystem_share
contributor_subsystem_cpoints   = contributor's C-points earned in that subsystem this month
total_subsystem_cpoints         = all contributors' C-points in that subsystem this month
contributor_incentive_payout    = (contributor_subsystem_cpoints / total_subsystem_cpoints)
                                  × subsystem_incentive_budget
```

A contributor's total monthly compensation is their base payout plus the sum of any incentive payouts across subsystems they contributed to.

**Conflict of interest:** Board members may not vote on the incentive allocation for any subsystem in which they rank in the top ten contributors by C-points over the preceding 24 months. Recusal is logged and published in the annual governance report (Charter §B.9.3).

### 7.4 Board Member Compensation

Board members are compensated from the operations slice (SCCL Article 4.7(f)), not from the contributor pools. Board compensation is set at **0.0001–0.001% of annual SCCL fees per Board member**, as determined by the Board and published in the annual financial report (Charter §B.5.5).

Board members who are also Active Contributors continue to earn contributor compensation through the base and incentive pools on equal footing with all other contributors. Their Board compensation is additive and separate.

### 7.5 Example

Annual SCCL fees: **$1,000,000**

| Pool | Annual | Monthly |
|---|---|---|
| Base pool (52%) | $520,000 | $43,333 |
| Incentive pool (5%) | $50,000 | $4,167 |
| Infrastructure (17%) | $170,000 | — |
| Reserve (12%) | $120,000 | — |
| Emergency (7%) | $70,000 | — |
| Operations (3%) | $30,000 | — |

In March, three contributors are active. The incentive allocation puts 35% of monthly incentive ($1,458) toward security work.

**Base pool payout (March):**

| Contributor | Monthly C-points | Share | Base payout |
|---|---|---|---|
| Alice | 36.0 | 40% | $17,333 |
| Bob | 27.0 | 30% | $13,000 |
| You | 27.0 | 30% | $13,000 |

**Incentive payout — security (March):** Alice and You both did security work; Bob did not.

| Contributor | Security C-points | Share | Incentive payout |
|---|---|---|---|
| Alice | 18.0 | 60% | $875 |
| You | 12.0 | 40% | $583 |

**Total March payout:** Alice $18,208 · You $13,583 · Bob $13,000.

---

*This methodology document is maintained by the Organization Board. Amendment proposals should be submitted as pull requests to this file with a corresponding Charter amendment reference.*
