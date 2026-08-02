from __future__ import annotations

from typing import Iterable


def brier_score(predictions: Iterable[float], outcomes: Iterable[int | bool]) -> float | None:
    values = [(float(prediction), int(bool(outcome))) for prediction, outcome in zip(predictions, outcomes)]
    return sum((prediction - outcome) ** 2 for prediction, outcome in values) / len(values) if values else None


def log_loss(predictions: Iterable[float], outcomes: Iterable[int | bool], epsilon: float = 1e-12) -> float | None:
    import math
    values = [(max(epsilon, min(1 - epsilon, float(prediction))), int(bool(outcome))) for prediction, outcome in zip(predictions, outcomes)]
    return -sum(outcome * math.log(prediction) + (1 - outcome) * math.log(1 - prediction) for prediction, outcome in values) / len(values) if values else None


def reliability_buckets(predictions: Iterable[float], outcomes: Iterable[int | bool], *, bins: int = 10) -> list[dict]:
    buckets = [{"count": 0, "predicted": 0.0, "observed": 0.0} for _ in range(bins)]
    for prediction, outcome in zip(predictions, outcomes):
        index = min(bins - 1, max(0, int(float(prediction) * bins)))
        bucket = buckets[index]
        bucket["count"] += 1
        bucket["predicted"] += float(prediction)
        bucket["observed"] += int(bool(outcome))
    for index, bucket in enumerate(buckets):
        count = bucket["count"]
        bucket.update({"bucket": index, "predicted_mean": bucket["predicted"] / count if count else None, "observed_rate": bucket["observed"] / count if count else None})
        bucket.pop("predicted")
        bucket.pop("observed")
    return buckets


def expected_calibration_error(predictions: Iterable[float], outcomes: Iterable[int | bool], *, bins: int = 10) -> float | None:
    buckets = reliability_buckets(predictions, outcomes, bins=bins)
    total = sum(bucket["count"] for bucket in buckets)
    return sum(bucket["count"] / total * abs(bucket["predicted_mean"] - bucket["observed_rate"]) for bucket in buckets if bucket["count"] and bucket["predicted_mean"] is not None and bucket["observed_rate"] is not None) if total else None
