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

Each month, the Board may publish a revised **monthly incentive allocation** — the percentage of the monthly incentive budget directed to each subsystem or task category. These percentages reflect strategic priorities (e.g., security-critical work, underserved subsystems, documentation gaps). If the Board does not publish a new allocation for a given month, the most recent allocation remains in effect.

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

### 7.5 Activation

The monthly compensation model activates at **Governance Stage 3** (Charter §B.9.3), when the project has 30+ Active Contributors and at least one active SCCL commercial license generating revenue. At Stages 1–2, there is no SCCL revenue to distribute. Stage 3 serves as the beta period for testing the compensation mechanics, refining the incentive allocation process, and establishing operational infrastructure (payment methods, tax compliance, contributor identity verification) before Stage 4 scales it to the full Governance Pool.

### 7.6 Example — Full Moneyfall

This example traces **$1,000,000** in annual SCCL license fees from collection to individual contributor payouts.

---

#### Step 1 — Annual fund allocation (SCCL Article 4.7)

| Category | % | Annual | Monthly (÷12) |
|---|---|---|---|
| **(a) Contributor base pool** | **52%** | **$520,000** | **$43,333** |
| **(b) Contributor incentive pool** | **5%** | **$50,000** | **$4,167** |
| (c) Infrastructure | 18% | $180,000 | — |
| (d) Reserve fund | 12% | $120,000 | — |
| (e) Emergency fund | 8% | $80,000 | — |
| (f) Operations (incl. Board comp.) | 5% | $50,000 | — |
| **Total** | **100%** | **$1,000,000** | |

Total contributor allocation: (a) + (b) = **57%** = **$570,000/year** = **$47,500/month**.

---

#### Step 2 — Board sets monthly incentive allocation

The Board publishes the incentive allocation for March (if unchanged from the previous month, the previous allocation carries over):

| Subsystem | Incentive share | Monthly incentive budget |
|---|---|---|
| Security / auth | 35% | $1,458 |
| Core dispatch engine | 25% | $1,042 |
| Documentation | 20% | $833 |
| TUI | 10% | $417 |
| Voice / STT | 10% | $417 |
| **Total** | **100%** | **$4,167** |

---

#### Step 3 — Contributors earn C-points in March

Three contributors are active. Their raw activity in March:

| Contributor | PRs merged | Reviews | Security patches | Doc PRs | Subsystems touched |
|---|---|---|---|---|---|
| Alice | 8 | 12 | 2 | 0 | dispatch, security |
| Bob | 5 | 6 | 0 | 3 | TUI, docs |
| You | 4 | 10 | 1 | 2 | security, docs, TUI |

---

#### Step 4 — Calculate monthly C-points per contributor

Using the 8-dimension formula (§1), each dimension normalized against the 95th-percentile contributor over the rolling 24-month window. For this example, assume the 95th-percentile ceilings are:

| Dimension | 95th-pctl ceiling (24-month) | Weight |
|---|---|---|
| Merged volume | 50 PRs | 20% |
| Code scope | 8,000 complexity-adjusted LOC | 20% |
| Review labor | 40 substantive reviews | 15% |
| Security work | 10 security patches | 15% |
| Maintenance | 20 triage actions | 10% |
| Documentation | 15 doc PRs | 10% |
| Mentorship | 30 mentoring interactions | 5% |
| Leadership | 10 events | 5% |

**Alice's March C-points** (showing only non-zero dimensions for brevity):

| Dimension | Raw value | Normalized (÷ ceiling × 100) | × Weight | Points |
|---|---|---|---|---|
| Merged volume | 8 PRs | (8/50) × 100 = 16.0 | × 0.20 | 3.20 |
| Code scope | 2,400 LOC | (2400/8000) × 100 = 30.0 | × 0.20 | 6.00 |
| Review labor | 12 reviews | (12/40) × 100 = 30.0 | × 0.15 | 4.50 |
| Security work | 2 patches | (2/10) × 100 = 20.0 | × 0.15 | 3.00 |
| **Alice total** | | | | **16.70** |

**Bob's March C-points:**

| Dimension | Raw value | Normalized | × Weight | Points |
|---|---|---|---|---|
| Merged volume | 5 PRs | 10.0 | × 0.20 | 2.00 |
| Code scope | 1,200 LOC | 15.0 | × 0.20 | 3.00 |
| Review labor | 6 reviews | 15.0 | × 0.15 | 2.25 |
| Documentation | 3 doc PRs | 20.0 | × 0.10 | 2.00 |
| **Bob total** | | | | **9.25** |

**Your March C-points:**

| Dimension | Raw value | Normalized | × Weight | Points |
|---|---|---|---|---|
| Merged volume | 4 PRs | 8.0 | × 0.20 | 1.60 |
| Code scope | 1,800 LOC | 22.5 | × 0.20 | 4.50 |
| Review labor | 10 reviews | 25.0 | × 0.15 | 3.75 |
| Security work | 1 patch | 10.0 | × 0.15 | 1.50 |
| Documentation | 2 doc PRs | 13.3 | × 0.10 | 1.33 |
| **Your total** | | | | **12.68** |

**Total monthly C-points (all contributors):** 16.70 + 9.25 + 12.68 = **38.63**

---

#### Step 5 — Base pool payout (March)

Monthly base budget: **$43,333**

| Contributor | C-points | Share (÷ 38.63) | Base payout |
|---|---|---|---|
| Alice | 16.70 | 43.2% | $18,733 |
| Bob | 9.25 | 23.9% | $10,373 |
| You | 12.68 | 32.8% | $14,227 |
| **Total** | **38.63** | **100%** | **$43,333** |

---

#### Step 6 — Incentive pool payout (March)

Each contributor's C-points are broken down by subsystem to determine incentive eligibility.

**Security incentive ($1,458):** Alice (2 patches) and You (1 patch) worked on security.

| Contributor | Security C-points | Share | Payout |
|---|---|---|---|
| Alice | 3.00 | 66.7% | $972 |
| You | 1.50 | 33.3% | $486 |

**Dispatch incentive ($1,042):** Only Alice worked on dispatch.

| Contributor | Dispatch C-points | Share | Payout |
|---|---|---|---|
| Alice | 6.00 | 100% | $1,042 |

**Documentation incentive ($833):** Bob and You wrote docs.

| Contributor | Docs C-points | Share | Payout |
|---|---|---|---|
| Bob | 2.00 | 60.0% | $500 |
| You | 1.33 | 40.0% | $333 |

**TUI incentive ($417):** Bob worked on TUI.

| Contributor | TUI C-points | Share | Payout |
|---|---|---|---|
| Bob | 3.00 | 100% | $417 |

**Voice/STT incentive ($417):** No contributors worked here this month. Unspent — returns to the reserve fund.

---

#### Step 7 — Total March compensation per contributor

| Contributor | Base payout | + Security | + Dispatch | + Docs | + TUI | **Total** |
|---|---|---|---|---|---|---|
| Alice | $18,733 | $972 | $1,042 | — | — | **$20,747** |
| Bob | $10,373 | — | — | $500 | $417 | **$11,290** |
| You | $14,227 | $486 | — | $333 | — | **$15,046** |
| **Monthly total** | **$43,333** | **$1,458** | **$1,042** | **$833** | **$417** | **$47,083** |

Unspent incentive (Voice/STT): $417 → reserve fund.

---

#### Step 8 — Board member compensation (separate)

From the operations slice ($50,000/year = $4,167/month), Board members receive 0.0001–0.001% of annual SCCL fees. At 0.0005% for a Board of 5:

```
per_board_member = $1,000,000 × 0.000005 = $5.00/year
```

This is intentionally nominal at low revenue. At $100M in annual fees, the same rate yields $500/year per Board member. Board members who are also Active Contributors continue earning contributor compensation through the base and incentive pools on equal footing.

---

*This methodology document is maintained by the Organization Board. Amendment proposals should be submitted as pull requests to this file with a corresponding Charter amendment reference.*
