# NHANES ETL Pipeline

## DAG: Sources → Transforms → Sinks

```
NHANES SAS XPT (*.XPT) → Read with pandas.read_sas() → Normalize (lowercase cols, trim whitespace) → Arrow Table → Write Parquet → ./warehouse/{table_name}.parquet
```

**Step breakdown:**
1. **Source**: SAS XPT files (NHANES public-health data format, fixed-width encoded)
2. **Extract**: `read_xpt()` loads via `pd.read_sas(format="xport")`
3. **Transform**: `normalize()` applies column lowercasing and string trimming
4. **Load**: `write_parquet()` converts to Arrow Table and writes compressed Parquet

## Usage

### Basic run (with synthetic fixture):
```bash
python nchs_etl.py
```
Generates synthetic NHANES demographics data (120 rows, 8 cols), converts to XPT, runs pipeline, outputs `./warehouse/nhanes_demo.parquet`.

### Programmatic usage:
```python
from nchs_etl import NHANESETLPipeline

pipeline = NHANESETLPipeline(warehouse_dir="./warehouse")
output_path = pipeline.run("DEMO_J.XPT", table_name="demo_j")
```

## Extending to multiple XPT files

Add a batch orchestrator:
```python
from pathlib import Path

def run_batch(xpt_dir: str, warehouse_dir: str = "./warehouse"):
    """Process all *.XPT files in a directory."""
    pipeline = NHANESETLPipeline(warehouse_dir=warehouse_dir)
    for xpt_file in Path(xpt_dir).glob("*.XPT"):
        table_name = xpt_file.stem.lower()
        pipeline.run(str(xpt_file), table_name=table_name)
        
# run_batch("/path/to/xpt/files")
```

Or integrate into Airflow DAG:
```python
from airflow import DAG
from airflow.operators.python import PythonOperator

with DAG("nhanes_etl_daily") as dag:
    for xpt in ["DEMO_J.XPT", "BMX_J.XPT", "BPX_J.XPT"]:
        PythonOperator(
            task_id=f"etl_{xpt.lower()}",
            python_callable=lambda f: NHANESETLPipeline().run(f),
            op_kwargs={"xpt_file": f"/data/xpt/{xpt}"}
        )
```

## Dependencies
- `pandas` (SAS reader)
- `pyarrow` (Parquet writer)

## Warehouse structure
```
warehouse/
├── nhanes_demo.parquet
├── demo_j.parquet
├── bmx_j.parquet
└── ...
```

Each table maintains source lineage via `table_name` (maps to XPT stem).
