# Karna LLC — Data Science & Analytics Team

## Company

Karna LLC is a public health consulting firm. Top-10 CDC contractor. NAICS: 541611, 541690, 541720, 541910, 518210, 541512. HIPAA-bound. All work is federal contract work under FAR/DFARS.

## Team

Data Science & Analytics — statistical analysis, survey data processing, surveillance systems, custom real-time data tools for CDC and other federal health agencies.

## Tech Stack

- Python (pandas, numpy, scipy, scikit-learn, matplotlib, seaborn)
- R (tidyverse, ggplot2, survey package)
- SAS (legacy CDC systems)
- SQL (PostgreSQL, SQL Server)
- Jupyter notebooks for exploratory analysis
- Git for version control

## Data Sources

- NCHS National Health Care Surveys
- BRFSS (Behavioral Risk Factor Surveillance System)
- CDC WONDER
- NNDSS (National Notifiable Diseases Surveillance System)
- Custom survey data (Qualtrics exports)
- EHR/medical records extracts (de-identified)

## Conventions

- All analysis scripts must be reproducible (no hardcoded paths)
- Use virtual environments (venv or conda)
- Branch naming: `feature/`, `analysis/`, `fix/`
- Every analysis needs a README explaining inputs, outputs, and methodology
- Code review required before delivering to clients

## Deliverable Formats

- CDC-formatted data briefs
- MMWR-style reports
- Statistical appendices with methodology sections
- Interactive dashboards (Tableau, Power BI)
- Cooperative agreement progress reports

## Rules for Nellie

- Never expose raw PHI — always work with de-identified datasets
- When generating SQL, always use parameterized queries
- Statistical outputs must include confidence intervals and p-values
- Flag when sample sizes are too small for reliable inference
- Use CDC citation style for references
