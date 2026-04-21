# Survey Weights Review: NHANES 2023 Extract

**Memo To:** Survey Operations Team  
**From:** Senior Statistician, Survey Quality Unit  
**Date:** 2024-01-15  
**Subject:** Weight Distribution & Stratum Precision Assessment

---

## Executive Summary

Weights for the NHANES 2023 extract exhibit acceptable statistical properties overall, with mild calibration concerns in two strata (Urban Non-Hispanic Black, Rural Other) that warrant targeted review before public release.

---

## Weight Distribution Summary

| Statistic | Value |
|-----------|-------|
| **Mean Weight** | 1,247.3 |
| **Median Weight** | 1,156.8 |
| **95th Percentile** | 3,421.5 |
| **Coefficient of Variation** | 0.412 |
| **Sum of Weights** | 330,847,240 |
| **Sample Size** | 265,230 |

The coefficient of variation (CV = 0.412) indicates moderate weight heterogeneity, consistent with stratified sampling and post-stratification adjustment. The ratio of 95th to median weight (2.96×) suggests acceptable tail behavior without extreme outliers.

---

## Stratum-Level Design Effects

Design effects exceeding 2.0 increase variance inflation and reduce effective sample size. Five key strata are reviewed below:

| Stratum | n_sampled | sum_wt | deff | reweight_flag |
|---------|-----------|--------|------|---------------|
| Urban, Non-Hispanic White | 68,450 | 89,240,156 | 1.68 | — |
| Urban, Non-Hispanic Black | 34,210 | 41,520,438 | 2.14 | ⚠️ |
| Rural, Non-Hispanic White | 51,870 | 68,935,270 | 1.72 | — |
| Rural, Non-Hispanic Black | 28,340 | 24,685,980 | 1.89 | — |
| Rural, Other Race/Ethnicity | 22,680 | 32,490,312 | 2.03 | ⚠️ |

Two strata exceed the conservative threshold:
- **Urban, Non-Hispanic Black** (deff = 2.14): Potential clustering or variance imbalance in urban metropolitan areas.
- **Rural, Other Race/Ethnicity** (deff = 2.03): Sparse stratum with unequal response patterns.

---

## Recommendations

1. **Immediate Action**: Conduct focused variance review on flagged strata; consider auxiliary variable smoothing or rake adjustment to marginalized Race/Ethnicity distributions.
2. **QA Checkpoint**: Verify stratum response rates and post-stratification targets before finalizing public-use dataset weights.

