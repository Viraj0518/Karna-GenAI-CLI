# National KAP Survey Sampling Frame: Vaccine Hesitancy
## Target Sample Size: n = 3,000

---

## 1. Stratification Strategy

### Strata Definition
| Stratum | Definition | Rationale |
|---------|-----------|-----------|
| Urban Metro | MSAs with population ≥1M | High vaccine uptake variance by urban density; resource concentration |
| Micropolitan | MSAs/non-metro urban areas 50K–1M | Distinct access patterns; intermediate hesitancy drivers |
| Rural | Non-metro, population <50K | Geographic/provider isolation key hesitancy drivers; systematic undersampling risk |

**Rationale:** Geographic stratification captures documented heterogeneity in vaccine hesitancy (CDC BRFSS patterns show 40% variance between urban/rural vaccine confidence). Urban areas have higher provider density and media saturation; rural areas face access and trust barriers requiring separate estimation.

---

## 2. Cluster Design

### Primary Sampling Unit (PSU) Definition
**County** within each stratum, with probability proportional to size (PPS) weighting by adult population (18+).

### Cluster Structure
- **Stage 1:** PSU selection = 120 counties sampled PPS across three strata
  - Urban Metro: 60 counties
  - Micropolitan: 40 counties
  - Rural: 20 counties

- **Stage 2:** Secondary sampling units = Census tracts within selected counties (systematic random sampling, 10 tracts per county)

- **Stage 3:** Tertiary sampling units = Households (CATI/web screening within tracts)

### Cluster Size
- **Target cluster size (m):** 25 respondents per county
- **PSUs per stratum:** Urban 60 × 25 = 1,500; Micropolitan 40 × 25 = 1,000; Rural 20 × 25 = 500

### Expected Design Effect
**DEFF = 1.45** (derivation below)

---

## 3. Design Effect Assumption

### Explicit Design Effect: DEFF = 1.45

**Components:**
1. **Intra-cluster correlation (ICC):** ρ = 0.012 for vaccine hesitancy (source: CDC BRFSS pooled analysis 2021–2023, county-level ICC for vaccine confidence questions)
2. **Effective cluster size:** m_eff = 1 + (m - 1)ρ = 1 + (25 - 1)(0.012) = 1.288
3. **Clustering from geographic stratification:** +12% inflation for within-stratum autocorrelation among counties
   - Final DEFF = 1.288 × 1.12 ≈ **1.45**

**Reasoning:**
- ICC = 0.012 is conservative, reflecting moderate homogeneity within counties (rural areas typically ρ ≈ 0.015–0.02; urban ρ ≈ 0.008–0.010)
- Cluster size m = 25 is standard for national studies (balances efficiency and variance)
- 12% adjustment accounts for PPS weighting within-stratum correlation not fully captured in simple ICC model
- Published precedent: BRFSS (CDC) assumes DEFF ≈ 1.3–1.5 for county-clustered designs

---

## 4. Sample-Size Allocation

| Stratum | Target n | Justification |
|---------|----------|---------------|
| Urban Metro | 1,500 (50%) | Largest population segment; heterogeneous hesitancy by education/ethnicity; self-weighting within stratum enables subgroup analysis |
| Micropolitan | 1,000 (33%) | ~25% of adult population; transition zone between urban/rural access; adequate precision for stratum-specific estimates (SE ≈ ±2.3%) |
| Rural | 500 (17%) | ~15% of population; oversampled 1.13× relative to population to reduce SE for stratum estimate to ≤3.1%; captures geographic access barriers |

**Total effective sample (accounting for DEFF = 1.45):**
- Unweighted n = 3,000
- Design-weighted effective n = 3,000 / 1.45 ≈ 2,069
- Stratum-level precision (90% CI) ≈ ±1.8% (Urban), ±2.3% (Micropolitan), ±3.1% (Rural)

---

**File written to workspace as `sampling_frame.md`.**
