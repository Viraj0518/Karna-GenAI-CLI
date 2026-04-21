import pandas as pd
import os


def transform_nchs_data(df: pd.DataFrame) -> pd.DataFrame:
    """Transform NCHS data: rename columns and compute MAP."""
    df = df.copy()
    df = df.rename(columns={
        "patient_id": "id",
        "age_yrs": "age",
        "sbp": "systolic",
        "dbp": "diastolic"
    })
    df["map"] = (df["systolic"] + 2 * df["diastolic"]) / 3
    return df


def main():
    input_csv = "fixture.csv"
    output_parquet = "output_refactored.parquet"

    # Create fixture if it doesn't exist
    if not os.path.exists(input_csv):
        df = pd.DataFrame({
            "patient_id": [1, 2, 3, 4],
            "age_yrs": [25, 45, 60, 35],
            "sbp": [120, 140, 155, 130],
            "dbp": [80, 90, 95, 85]
        })
        df.to_csv(input_csv, index=False)

    df = pd.read_csv(input_csv)
    df = transform_nchs_data(df)
    df.to_parquet(output_parquet, index=False)
    print(f"ETL complete. Output: {output_parquet}")


if __name__ == "__main__":
    main()
