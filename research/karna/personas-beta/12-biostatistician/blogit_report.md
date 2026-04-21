# 30-Day Readmission: Multilevel Logistic Regression Analysis

## Study Overview

This analysis examines 30-day hospital readmission in 240 patients nested within 8 clinics. We fit a mixed-effects logistic regression model to account for clustering of patients within clinics and to estimate clinic-level heterogeneity in readmission risk.

**Dataset:** 240 patients across 8 clinics (30 per clinic); **Overall readmission rate:** 57.1%

---

## Model Specification

**Outcome:** 30-day readmission (binary: 0 = no, 1 = yes)  
**Fixed effects:** Age (centered), number of comorbidities (centered)  
**Random effects:** Random intercept by clinic  
**Method:** Generalized Estimating Equations (GEE) with logit link and exchangeable correlation structure

---

## Key Results

### Fixed Effects (Log-Odds Scale)

| Predictor | Coefficient | 95% CI | Odds Ratio | 95% CI (OR) |
|:-----------|:---:|:---:|:---:|:---:|
| **Intercept** | 0.340 | (−0.072, 0.751) | 1.40 | (0.93, 2.12) |
| **Age (centered)** | 0.055 | (0.036, 0.074) | 1.057 | (1.037, 1.077) |
| **Comorbidities (centered)** | 0.303 | (0.104, 0.502) | 1.353 | (1.109, 1.651) |

### Variance Components

| Component | Estimate |
|:-----------|:---:|
| **Between-clinic variance (τ²)** | 0.461 |
| **Intraclass Correlation (ICC)** | 0.123 |

The ICC of 0.123 indicates that approximately 12.3% of the variation in readmission risk is attributable to differences between clinics, with the remaining variation due to individual patient factors.

---

## Plain-Language Interpretation

**For a clinical audience:**

We analyzed what factors predict whether patients are readmitted to the hospital within 30 days of discharge. The analysis included 240 patients from 8 different clinics. Our key findings:

**Age matters:** For every additional year of age, a patient's odds of readmission increase by about 5.7%. Put another way, a 70-year-old patient has roughly 1.6 times higher odds of readmission compared to a 60-year-old patient with similar comorbidity status—a clinically meaningful increase that should inform post-discharge care planning.

**Comorbidities matter more:** Each additional chronic condition roughly increases a patient's readmission odds by 35%. A patient with 3 comorbidities has about 2.2 times higher odds of readmission than a patient with no comorbidities, holding age constant. This underscores the importance of robust disease management and follow-up care for complex patients.

**Clinics differ, but consistently:** About 12% of variation in readmission rates across patients is due to systematic differences between clinics (e.g., discharge processes, follow-up protocols). The remaining 88% is driven by individual patient characteristics. This suggests that while clinic-level interventions (better follow-up scheduling, coordinated care) could improve outcomes, individual patient complexity remains the dominant driver of readmission risk.

---

## Model Diagnostics & Interpretation Notes

- **GEE approach:** Uses an exchangeable working correlation structure, appropriate for clustered binary data and robust to misspecification of the within-clinic correlation.
- **Centered predictors:** Age and comorbidity count were centered at their sample means (age mean = 64.8 years, comorbidity mean = 1.85) to make the intercept interpretable as the average risk on the log-odds scale.
- **95% CIs:** Reflect 95% confidence intervals based on robust sandwich estimators.

---

*Analysis completed using Python's statsmodels GEE framework. Code and data available upon request.*
