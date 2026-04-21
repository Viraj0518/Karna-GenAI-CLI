"""
NHANES SAS XPT → Parquet ETL pipeline.
Reads simulated NHANES XPT files, normalizes, writes to warehouse.
"""

import os
import logging
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class NHANESETLPipeline:
    """ETL orchestrator for NHANES data."""

    def __init__(self, warehouse_dir: str = "./warehouse"):
        self.warehouse_dir = Path(warehouse_dir)
        self.warehouse_dir.mkdir(parents=True, exist_ok=True)

    def read_xpt(self, file_path: str) -> pd.DataFrame:
        """Read SAS XPT file using pandas or fallback for testing."""
        logger.info(f"Reading XPT: {file_path}")
        try:
            df = pd.read_sas(file_path, format="xport")
        except (OSError, ValueError):
            # Fallback: if file is parquet (for testing without SAS libraries)
            if file_path.endswith(".parquet"):
                df = pd.read_parquet(file_path)
            else:
                raise
        logger.info(f"Loaded {len(df)} rows, {len(df.columns)} columns")
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply basic normalization: lowercase col names, trim whitespace."""
        logger.info("Normalizing data")
        df.columns = df.columns.str.lower().str.strip()
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].str.strip()
        return df

    def write_parquet(self, df: pd.DataFrame, table_name: str) -> Path:
        """Write DataFrame to Parquet in warehouse."""
        output_path = self.warehouse_dir / f"{table_name}.parquet"
        logger.info(f"Writing to {output_path}")
        table = pa.Table.from_pandas(df)
        pq.write_table(table, output_path)
        logger.info(f"Wrote {len(df)} rows to {output_path}")
        return output_path

    def run(self, xpt_file: str, table_name: str = None) -> Path:
        """Execute full pipeline: read → normalize → write."""
        if table_name is None:
            table_name = Path(xpt_file).stem
        df = self.read_xpt(xpt_file)
        df = self.normalize(df)
        return self.write_parquet(df, table_name)


def create_synthetic_nhanes_fixture(n_rows: int = 100) -> pd.DataFrame:
    """
    Create synthetic NHANES-like demographics data.
    Mirrors structure of NHANES demo files (DEMO_*.XPT).
    """
    import numpy as np

    seqn = np.arange(1, n_rows + 1)

    data = {
        "SEQN": seqn,
        "SDDSRVYR": np.random.choice([9, 10, 11], n_rows),  # Survey year code
        "RIDSTATR": np.random.choice([1, 2], n_rows),  # Interview status
        "RIDAGEYY": np.random.randint(0, 80, n_rows),  # Age in years
        "RIAGENDR": np.random.choice([1, 2], n_rows),  # Gender (1=M, 2=F)
        "RIDRACEP": np.random.choice([1, 2, 3, 4, 5], n_rows),  # Race/Ethnicity
        "DMDMARTL": np.random.choice([1, 2, 3, 4, 5, 6, 77, 99], n_rows),  # Marital status
        "DMDEDUC2": np.random.choice([1, 2, 3, 4, 5, 9], n_rows),  # Education level
    }

    return pd.DataFrame(data)


if __name__ == "__main__":
    # Create synthetic fixture and run pipeline
    synthetic_df = create_synthetic_nhanes_fixture(n_rows=120)
    synthetic_xpt_path = "/tmp/synthetic_demo.parquet"

    # Write synthetic data as parquet (simulates XPT for demo)
    synthetic_df.to_parquet(synthetic_xpt_path)
    logger.info(f"Created synthetic fixture: {synthetic_xpt_path}")

    # Initialize pipeline and run
    pipeline = NHANESETLPipeline(warehouse_dir="./warehouse")
    output_path = pipeline.run(synthetic_xpt_path, table_name="nhanes_demo")
    
    logger.info(f"✓ Pipeline complete. Output: {output_path}")

    # Verify
    result_table = pq.read_table(output_path)
    logger.info(f"Verification: {result_table.num_rows} rows, {result_table.num_columns} columns")
