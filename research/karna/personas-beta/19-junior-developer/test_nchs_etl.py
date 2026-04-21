import pandas as pd
import pytest
from nchs_etl_refactored import transform_nchs_data


@pytest.fixture
def sample_data():
    """Sample NCHS data fixture."""
    return pd.DataFrame({"patient_id": [1, 2, 3], "age_yrs": [25, 45, 60], "sbp": [120, 140, 155], "dbp": [80, 90, 95]})


def test_column_renaming(sample_data):
    """Test that columns are renamed correctly."""
    result = transform_nchs_data(sample_data)
    expected_cols = {"id", "age", "systolic", "diastolic", "map"}
    assert set(result.columns) == expected_cols


def test_map_calculation(sample_data):
    """Test that MAP is calculated correctly: (systolic + 2*diastolic) / 3."""
    result = transform_nchs_data(sample_data)
    expected_map = (sample_data["sbp"] + 2 * sample_data["dbp"]) / 3
    pd.testing.assert_series_equal(result["map"], expected_map, check_names=False)


def test_data_integrity(sample_data):
    """Test that original data is preserved in renamed columns."""
    result = transform_nchs_data(sample_data)
    pd.testing.assert_series_equal(result["id"], sample_data["patient_id"], check_names=False)
    pd.testing.assert_series_equal(result["age"], sample_data["age_yrs"], check_names=False)
    pd.testing.assert_series_equal(result["systolic"], sample_data["sbp"], check_names=False)
    pd.testing.assert_series_equal(result["diastolic"], sample_data["dbp"], check_names=False)


def test_no_side_effects(sample_data):
    """Test that input dataframe is not modified."""
    original_cols = set(sample_data.columns)
    transform_nchs_data(sample_data)
    assert set(sample_data.columns) == original_cols
