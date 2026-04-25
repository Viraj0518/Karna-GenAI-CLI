# PROSPERO Protocol Review: Systematic Review of COVID-19 Vaccine Effectiveness in Immunocompromised Populations

**Reviewer:** Research Methodologist, Karna  
**Date:** 2024  
**Protocol ID (Hypothetical):** CRD42024XXXXXX

---

## Executive Summary
This protocol demonstrates foundational systematic review structure but contains critical gaps in search strategy rigor, inclusion criteria precision, and bias assessment methodology. Five priority issues identified below require remediation before registration.

---

## PRISMA Alignment Assessment

| Item | Status | Comments |
|------|--------|----------|
| Objectives & PICO | ✓ Adequate | Population, intervention, and outcomes defined |
| Eligibility Criteria | ✗ **ISSUE #1** | Imprecise language; see details below |
| Search Strategy | ✗ **ISSUE #2** | Missing database selection rationale; see details |
| Study Selection | ✓ Adequate | Two-stage screening with COVIDENCE planned |
| Risk of Bias | ✗ **ISSUE #3** | Tool selection unjustified; see details |
| Data Extraction | ⚠ Partial | No pilot-testing protocol specified |
| Synthesis Methods | ✗ **ISSUE #4** | Heterogeneity thresholds undefined; see details |
| Publication Bias | ✗ **ISSUE #5** | Egger's test only; missing funnel plot interpretation rules |

---

## Issue #1: Inclusion Criteria Lack Specificity

### Problem
**Current language:** "Studies examining vaccine effectiveness among people with compromised immune systems."

- **Vague definitions:** No specification of immunocompromised states (e.g., HIV CD4 <200, transplant recipients, chemotherapy-active patients).
- **Effectiveness ambiguity:** Does this include IgG seroconversion, infection prevention, hospitalization prevention, or mortality?
- **Study design scope:** Protocol states "RCTs and observational studies" but doesn't specify minimum sample size or follow-up duration.

### Risk
Reviewers will face inconsistent interpretation, reducing reproducibility and increasing selection bias.

### Recommended Fix
Replace with operationalized criteria:

**Population:** Adults (≥18 years) with documented immunocompromise defined as: (a) HIV CD4 <200 cells/μL, (b) post-organ/stem-cell transplant recipients, (c) active chemotherapy/hematologic malignancy, or (d) on ≥20mg/day prednisone equivalent.

**Primary outcome:** SARS-CoV-2 infection prevention (RT-PCR or antigen-confirmed); secondary: hospitalization, death, or breakthrough infection during ≥12 months follow-up.

**Study designs:** RCTs and prospective cohort studies with ≥100 participants and ≥6-month follow-up. Exclude: case reports, case series, retrospective cohorts <6 months.

---

## Issue #2: Search Strategy Missing Database Rationale & Syntax Completeness

### Problem
- **Database selection:** Lists "PubMed, Embase, Scopus" without justification. Why exclude CINAHL (nursing evidence) or WHO COVID-19 database?
- **Search syntax incomplete:** Protocol shows only PubMed terms; Embase syntax, controlled vocabulary mapping, and Boolean operator applications not detailed.
- **Language/date limits undefined:** States "2020-2024" but no justification for start date or language restriction policy.
- **Grey literature absent:** No mention of trial registries (ClinicalTrials.gov), preprints (medRxiv), or contact with experts for unpublished data.

### Risk
Non-reproducible searches; risk of missing relevant trials; publication bias inflated.

### Recommended Fix
- **Justify database selection:** Include PubMed (free access, clinician reach), Embase (broader pharmacology/immunology indexing), ClinicalTrials.gov (trial registry), and medRxiv (preprints posted within 12 months of search).
- **Provide complete MESH/Emtree syntax:**
  
  ```
  PubMed: (COVID-19 OR SARS-CoV-2) AND (vaccine OR vaccination) AND (immunocompromised OR immunosuppressed OR "CD4 count" OR transplant OR chemotherapy) AND (2020:2024[pdat])
  
  Embase: COVID-19 NEAR/3 vaccine AND (immunocompromised OR immunosuppressed OR transplant) AND [2020-2024]/py
  ```

- **Define search strategy peer review:** State that search will be peer-reviewed using PRESS checklist before execution.
- **Grey literature protocol:** Specify contacting ≥10 vaccine manufacturers for unpublished trial data; hand-search ≥5 key conference proceedings (IDSA, AST).

---

## Issue #3: Risk-of-Bias Tool Selection Unjustified

### Problem
- **Tool choice unexplained:** Protocol states "ROB 2 for RCTs; ROBINS-I for observational studies" but provides no justification.
- **Applicability:** ROB 2 focuses on internal validity bias domains; no mention of whether external validity (generalizability to clinical practice) will be assessed.
- **Conflict of interest assessment:** No strategy for evaluating author-industry relationships or funding bias.
- **Blinding domain inadequacy:** Vaccine studies cannot be double-blind; protocol doesn't describe how ROB 2's "detection bias" domain will be adapted.

### Risk
Incomplete bias characterization; reviewers may over-weight or under-weight studies incorrectly.

### Recommended Fix
- **Explicitly justify tool selection:**

  > "ROB 2 selected because it aligns with Cochrane standards and includes five bias domains applicable to vaccine trials (selection, performance, detection, attrition, reporting). For observational studies, ROBINS-I chosen for its causal framework assessment covering confounding and selection bias."

- **Operationalize blinding domain:** 
  
  > "Detection bias evaluated separately: if outcome assessment was objective (PCR testing) and independent of allocation, score 'Low risk.' If outcome assessor knew allocation (self-reported infection), score 'Some concerns' unless adjustment for detection methods is documented."

- **Add conflict-of-interest screening:** Require extraction of funding source, author disclosures, and affiliation with vaccine manufacturers. Studies with undisclosed conflicts flagged in sensitivity analysis.

---

## Issue #4: Heterogeneity Thresholds and Synthesis Methods Undefined

### Problem
- **I² interpretation absent:** No a priori thresholds for deciding between fixed-effects and random-effects meta-analysis.
- **Clinical heterogeneity unaddressed:** Protocol doesn't specify which variables (immunocompromise type, vaccine platform, outcome definition) justify stratified analysis vs. pooling.
- **Fallback plan missing:** No description of when meta-analysis will be inappropriate or when narrative synthesis will be preferred.

### Risk
Post-hoc decisions on I² cutoffs introduce researcher degrees-of-freedom; results lack transparency.

### Recommended Fix
Define a priori heterogeneity management:

**Statistical heterogeneity thresholds:**
- I² <40%: Fixed-effects meta-analysis preferred.
- I² 40–75%: Random-effects meta-analysis; investigate sources.
- I² >75%: Meta-analysis not performed; narrative synthesis with forest plot presented for visual appraisal.

**Clinical heterogeneity stratification (planned a priori):**
1. By immunocompromise category (HIV vs. transplant vs. chemotherapy).
2. By vaccine platform (mRNA vs. viral vector vs. protein subunit).
3. By primary outcome (infection vs. hospitalization).

**Narrative synthesis criteria:**
If ≤3 studies per stratum or if study populations differ qualitatively (e.g., vaccine dosing schedules not harmonizable), use narrative synthesis with structured summary tables and GRADE certainty assessment.

---

## Issue #5: Publication Bias Assessment—Egger's Test Insufficient

### Problem
- **Single test dependency:** Protocol relies only on Egger's regression test; doesn't specify α threshold (0.05? 0.10?).
- **Funnel plot interpretation rules absent:** No guidance on symmetry assessment, trim-and-fill application, or how asymmetry will inform recommendations.
- **Sample size caveat ignored:** Egger's test underpowered if <10 studies included; protocol doesn't acknowledge this limitation.
- **Bias mechanism unspecified:** No distinction between publication bias (small negative studies unpublished) vs. other biases (outcome reporting, selective analysis).

### Risk
False confidence in absence of publication bias; inflated effect estimates drive policy recommendations.

### Recommended Fix
**Multi-tier publication bias assessment:**

1. **Quantitative tests (if ≥10 studies pooled):**
   - Egger's regression (α = 0.10, two-tailed) + Begg's rank correlation as sensitivity check.
   - Report 95% CI around intercept; interpret asymmetry in context of clinical heterogeneity.

2. **Funnel plot inspection (all comparisons):**
   - Visual assessment: if <5 studies, note test inadequacy in funnel plot caption.
   - Define a priori: studies 2+ SEs above regression line flagged as potential outliers; explore reasons (study quality, population differences, outcome measurement).

3. **Trim-and-fill analysis (supplementary):**
   - If asymmetry detected, impute missing studies and recalculate effect estimate and CI.
   - Report imputed estimate alongside observed; interpret as worst-case scenario.

4. **GRADE certainty downgrade:**
   - If publication bias suspected (asymmetric funnel, |Egger intercept| >1.96 SE), downgrade certainty of evidence by one level in final assessments.

5. **Outcome reporting bias (separate from publication bias):**
   - Compare registered trial protocols (ClinicalTrials.gov) against published reports for primary outcome changes.
   - Extract pre-specified outcomes; document any post-hoc outcome additions.

---

## Summary of Required Amendments

| Issue | Priority | Amendment |
|-------|----------|-----------|
| #1 | **High** | Operationalize immunocompromised populations, effectiveness definitions, study designs |
| #2 | **High** | Provide complete search syntax for all databases; justify grey literature strategy |
| #3 | **Medium** | Justify bias tool selection; operationalize blinding & COI assessment |
| #4 | **High** | Define I² thresholds and clinical stratification criteria a priori |
| #5 | **Medium** | Expand publication bias plan beyond Egger's; specify interpretation rules |

---

## Recommendation
**Conditional approval** pending revision of Issues #1, #2, and #4. Authors should resubmit with amendments addressing search strategy completeness, inclusion criteria operationalization, and heterogeneity thresholds. Secondary amendments (#3, #5) strengthen robustness but are not blockers.

Protocol revision estimated at 2–3 weeks. Resubmit to PROSPERO within 60 days of this review.

---

**Completed:** Prospero protocol review with 5 flagged issues and remediation guidance provided.
