"""
Outbreak signal classifier on synthetic NNDSS dataset.
Detects anomalous weeks using weak-signal classification.
"""

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import precision_score, recall_score, classification_report
import joblib

# Set random seed for reproducibility
np.random.seed(42)

# Generate synthetic NNDSS-shaped dataset
diseases = ["Influenza", "RSV", "COVID-19", "Measles", "Pertussis"]
jurisdictions = ["NSW", "VIC", "QLD", "WA", "SA", "NT", "ACT"]
weeks = 52

n_rows = len(diseases) * len(jurisdictions) * weeks  # 1820 rows

data = []
for disease in diseases:
    for jurisdiction in jurisdictions:
        for week in range(1, weeks + 1):
            # Base case count with seasonal pattern
            base = np.random.poisson(5 + 3 * np.sin(2 * np.pi * week / 52))
            
            # Cases with random variation
            cases = max(0, base + np.random.normal(0, 1))
            
            # Prior week cases for trend
            prior_cases = max(0, base + np.random.normal(0, 1))
            
            # Cases 4 weeks prior for yearly comparison
            prior_4w_cases = max(0, base + np.random.normal(0, 1))
            
            # Calculate percent change
            pct_change = ((cases - prior_cases) / (prior_cases + 1)) * 100
            pct_change_4w = ((cases - prior_4w_cases) / (prior_4w_cases + 1)) * 100
            
            # Population-based rate (per 100k)
            population = np.random.randint(500000, 5000000)
            rate_per_100k = (cases / population) * 100000
            
            # Anomaly flag: outbreak if high cases + positive trend or sudden spike
            is_outbreak = (cases > 12 and pct_change > 20) or (cases > 15 and pct_change_4w > 40) or np.random.random() < 0.12
            
            data.append({
                "disease": disease,
                "jurisdiction": jurisdiction,
                "week": week,
                "cases": cases,
                "prior_week_cases": prior_cases,
                "prior_4w_cases": prior_4w_cases,
                "pct_change": pct_change,
                "pct_change_4w": pct_change_4w,
                "rate_per_100k": rate_per_100k,
                "population": population,
                "anomalous": int(is_outbreak)
            })

df = pd.DataFrame(data)

print(f"Dataset shape: {df.shape}")
print(f"Anomalous weeks: {df['anomalous'].sum()} ({df['anomalous'].mean()*100:.1f}%)")
print(f"\nDisease distribution:\n{df['disease'].value_counts()}")
print(f"\nJurisdiction distribution:\n{df['jurisdiction'].value_counts()}")

# Feature engineering
feature_cols = ["cases", "prior_week_cases", "prior_4w_cases", "pct_change", "pct_change_4w", "rate_per_100k"]
X = df[feature_cols].copy()
y = df["anomalous"]

# Add interaction features
X["cases_x_pct_change"] = X["cases"] * X["pct_change"]
X["rate_x_pct_change_4w"] = X["rate_per_100k"] * X["pct_change_4w"]
X["cases_ratio"] = X["cases"] / (X["prior_week_cases"] + 1)

# One-hot encode categorical features
X_disease = pd.get_dummies(df["disease"], prefix="disease", drop_first=True)
X_jurisdiction = pd.get_dummies(df["jurisdiction"], prefix="jurisdiction", drop_first=True)
X = pd.concat([X, X_disease, X_jurisdiction], axis=1)

# Split train/test
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

# Scale features
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

# Train gradient boosting classifier
model = GradientBoostingClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, random_state=42)
model.fit(X_train_scaled, y_train)

# Predict probabilities and apply threshold
y_pred_proba = model.predict_proba(X_test_scaled)[:, 1]
threshold = 0.15  # Lower threshold for public health surveillance (prioritize recall)
y_pred = (y_pred_proba >= threshold).astype(int)

# Evaluate
precision = precision_score(y_test, y_pred)
recall = recall_score(y_test, y_pred)

print(f"\n--- Model Performance (threshold={threshold}) ---")
print(f"Precision: {precision:.3f}")
print(f"Recall: {recall:.3f}")
print(f"\nClassification Report:")
print(classification_report(y_test, y_pred, target_names=["Normal", "Outbreak"]))

# Save model
joblib.dump(model, "outbreak_model.joblib")
print(f"\nModel saved to outbreak_model.joblib")
