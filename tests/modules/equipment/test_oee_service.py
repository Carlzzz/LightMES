from lightmes.modules.equipment.oee_service import (
    compute_availability, compute_quality, compute_oee,
)


def test_compute_availability():
    # 8h shift, 1h unplanned downtime → (8-1)/8 = 87.5%
    assert abs(compute_availability(8 * 3600, 1 * 3600) - 0.875) < 1e-6


def test_compute_availability_no_downtime():
    assert compute_availability(8 * 3600, 0) == 1.0


def test_compute_availability_zero_shift():
    assert compute_availability(0, 0) == 0.0


def test_compute_quality():
    # 100 produced, 5 scrapped → 95/100 = 95%
    assert abs(compute_quality(100, 5) - 0.95) < 1e-6


def test_compute_quality_zero_produced():
    assert compute_quality(0, 0) == 0.0


def test_compute_oee():
    assert abs(compute_oee(0.875, 0.95) - 0.83125) < 1e-6
