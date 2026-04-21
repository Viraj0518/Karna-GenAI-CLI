import pandas as pd
import os

# ETL stub: read CSV, rename columns, compute derived column, write Parquet

input_csv = "fixture.csv"
output_parquet = "output_stub.parquet"

# Create fixture if it doesn't exist
if not os.path.exists(input_csv):
    df = pd.DataFrame({
        "patient_id": [1, 2, 3, 4],
        "age_yrs": [25, 45, 60, 35],
        "sbp": [120, 140, 155, 130],
        "dbp": [80, 90, 95, 85]
    })
    df.to_csv(input_csv, index=False)

# Read CSV
df = pd.read_csv(input_csv)

# Rename columns
df = df.rename(columns={
    "patient_id": "id",
    "age_yrs": "age",
    "sbp": "systolic",
    "dbp": "diastolic"
})

# Compute derived column: mean arterial pressure
df["map"] = (df["systolic"] + 2 * df["diastolic"]) / 3

# Write Parquet
df.to_parquet(output_parquet, index=False)

print(f"ETL complete. Output: {output_parquet}")
