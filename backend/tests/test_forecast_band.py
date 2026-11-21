"""T063: the 80 % band of the liquidity forecast comes from measured cumulative errors of the rolling-origin folds
(`cfo.forecast.band`), is stored in the model metadata, and old models without it keep the sqrt formula."""

from __future__ import annotations

import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cfo.api import services
from cfo.api.schemas import ForecastPoint
from cfo.data.source import SourceNotFound
from cfo.forecast import band as fb
from cfo.forecast import evaluate as ev
from cfo.forecast import model as m

LEGACY_Z = 1.2816


def _flat_history(start="2025-01-01", end="2025-12-31", daily=1000.0, wobble=0.0) -> pd.DataFrame:
    """One revenue booking per day; `wobble` adds a deterministic day-to-day noise (so a model has a real error)."""
    df = pd.DataFrame([(d, daily + wobble * math.sin(7.3 * i), "revenue", None)
                       for i, d in enumerate(pd.date_range(start, end))],
                      columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


class _Stub:
    excluded_categories = m.EXCLUDED_CATEGORIES

    def __init__(self, fn):
        self.fn = fn

    def predict(self, dates):
        idx = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize().rename("date")
        return pd.Series([self.fn(d) for d in idx], index=idx, name="predicted_net")


def _low_by_100(_train) -> _Stub:
    """A trainer that is 100 EUR/day too low on a flat 1000 EUR/day history."""
    return _Stub(lambda _d: 900.0)


def _model(history: pd.DataFrame | None = None, **kw) -> m.ForecastModel:
    return m.train(history if history is not None else _flat_history("2025-01-01", "2025-11-30", wobble=200.0),
                   log_to_mlflow=False, **kw)


# -- the fit ----------------------------------------------------------------------------------------------
def test_band_of_a_known_bias_contains_the_truth_and_the_sqrt_band_does_not():
    tx = _flat_history()
    spec = fb.fit_band(ev.rolling_origin(tx, _low_by_100))
    model = _model(band=False)
    model.band_spec = spec
    model.metrics = {"holdout_rmse": 100.0}  # what the old formula would use for a model this good
    truth = 100.0 * 30  # actual balance minus predicted balance after 30 days
    lower, upper = model.band(30)
    assert lower <= truth <= upper
    legacy = _model(band=False)
    legacy.metrics = {"holdout_rmse": 100.0}
    old_lower, old_upper = legacy.band(30)
    assert not old_lower <= truth <= old_upper
    assert old_upper == pytest.approx(LEGACY_Z * 100.0 * math.sqrt(30))


def test_fit_needs_two_origins_and_returns_none_otherwise():
    folds = ev.rolling_origin(_flat_history(), _low_by_100, origins=["2025-06-30"])
    assert fb.fit_band(folds) is None


def test_spec_is_compact_json_with_anchor_horizons():
    spec = fb.fit_band(ev.rolling_origin(_flat_history(), _low_by_100))
    assert json.loads(json.dumps(spec)) == spec
    assert {"7", "14", "30", "60", "90"} <= set(spec["anchors"])
    assert spec["n_origins"] == 6 and spec["level"] == 0.8


def test_band_is_asymmetric_when_the_errors_are_biased():
    # model too HIGH by 100 EUR/day: the actual balance ends below the forecast, so the lower side is the wide one
    spec = fb.fit_band(ev.rolling_origin(_flat_history(), lambda _t: _Stub(lambda _d: 1100.0)))
    lower, upper = fb.offsets(spec, 30)
    assert lower <= -100.0 * 30 and upper < abs(lower) / 4


@pytest.mark.parametrize("horizon", [1, 7, 30, 90, 91, 180, 365, 1000])
def test_offsets_bracket_the_forecast_and_width_never_shrinks(horizon):
    folds = ev.rolling_origin(_flat_history(), lambda _t: _Stub(lambda d: 1000.0 + 300 * math.sin(d.day_of_year)))
    spec = fb.fit_band(folds)
    lower, upper = fb.offsets(spec, horizon)
    nxt_lower, nxt_upper = fb.offsets(spec, horizon + 1)
    assert lower <= 0.0 <= upper
    assert nxt_lower <= lower and nxt_upper >= upper


def test_growth_continues_past_the_measured_horizon_without_flattening():
    spec = fb.fit_band(ev.rolling_origin(_flat_history(), lambda _t: _Stub(lambda _d: 1100.0)))
    measured = spec["horizon_measured"]
    _, up1 = fb.offsets(spec, measured)
    low1, _ = fb.offsets(spec, measured)
    low2, _ = fb.offsets(spec, measured * 2)
    assert low2 < low1 and abs(low2) >= abs(low1) * (1 + 0.5)  # at least half-way to linear growth
    assert up1 >= 0.0


def test_growth_exponent_is_clamped():
    spec = fb.fit_band(ev.rolling_origin(_flat_history(), _low_by_100))
    assert fb.MIN_GROWTH <= spec["p"] <= fb.MAX_GROWTH


# -- the model --------------------------------------------------------------------------------------------
def test_train_computes_the_band_by_default_and_not_inside_its_folds(monkeypatch):
    seen: list[m.ForecastModel] = []
    real = ev.rolling_origin

    def spy(tx, trainer, **kw):
        def wrapped(train_tx):
            fold_model = trainer(train_tx)
            seen.append(fold_model)
            return fold_model
        return real(tx, wrapped, **kw)

    monkeypatch.setattr(ev, "rolling_origin", spy)
    model = _model(_flat_history("2025-01-01", "2025-12-31", wobble=200.0), holdout_days=0)
    assert model.band_spec is not None and model.band_spec["n_origins"] >= 2
    assert seen and all(f.band_spec is None for f in seen)  # no recursion: folds train without a band


def test_band_false_keeps_the_old_formula():
    model = _model(_flat_history("2025-01-01", "2025-12-31", wobble=200.0), band=False)
    assert model.band_spec is None
    assert "band" not in model.meta() or model.meta()["band"] is None


def test_too_short_history_has_no_band_but_still_trains():
    model = _model(_flat_history("2025-01-01", "2025-04-30"))
    assert model.band_spec is None
    lower, upper = model.band(10)
    assert lower <= 0 <= upper


def test_band_survives_meta_roundtrip_and_save_load(tmp_path):
    model = _model(_flat_history("2025-01-01", "2025-12-31", wobble=200.0))
    assert model.band_spec is not None
    rebuilt = m.ForecastModel.from_meta(model.regressor, json.loads(json.dumps(model.meta())))
    assert rebuilt.band_spec == model.band_spec
    assert rebuilt.band(45) == model.band(45)
    path = model.save(tmp_path / "x.json")
    assert m.ForecastModel.load(path).band(45) == model.band(45)


def test_old_format_model_yields_the_old_band():
    model = _model(band=False)
    meta = model.meta()
    meta.pop("band", None)  # a file written before T063
    old = m.ForecastModel.from_meta(model.regressor, meta)
    assert old.band_spec is None
    sigma = old.metrics["holdout_rmse"]
    for n in (1, 7, 30, 365):
        lower, upper = old.band(n)
        assert upper == pytest.approx(LEGACY_Z * sigma * math.sqrt(n))
        assert lower == pytest.approx(-upper)


def test_old_format_without_holdout_rmse_uses_the_train_mae():
    model = _model(band=False, holdout_days=0)
    assert "holdout_rmse" not in model.metrics
    lower, upper = model.band(16)
    assert upper == pytest.approx(LEGACY_Z * model.metrics["train_mae"] * 1.25 * 4)


def test_refreshed_model_keeps_the_band():
    history = _flat_history("2025-01-01", "2025-12-31", wobble=200.0)
    model = _model(history)
    assert model.level_values and model.band_spec is not None
    longer = pd.concat([history, _flat_history("2026-01-01", "2026-01-31", daily=1300.0, wobble=200.0)],
                       ignore_index=True)
    refreshed = model.with_levels_from(longer)
    assert refreshed is not model and refreshed.level_values != model.level_values
    assert refreshed.band_spec == model.band_spec
    assert refreshed.band(30) == model.band(30) and refreshed.band(30)[1] > 0


def test_band_of_a_short_history_that_falls_back_to_calendar_does_not_crash():
    short = _flat_history("2025-12-01", "2025-12-10", wobble=200.0)  # too short for a level model, no folds either
    model = m.train(short, log_to_mlflow=False)
    assert model.feature_set == "calendar" and model.band_spec is None
    lower, upper = model.band(30)
    assert upper == pytest.approx(LEGACY_Z * model.metrics["train_mae"] * 1.25 * math.sqrt(30)) and lower == -upper


def test_calendar_model_of_a_long_history_gets_a_band_too():
    model = _model(_flat_history("2025-01-01", "2025-12-31", wobble=200.0), feature_set="calendar")
    assert model.band_spec is not None and model.feature_set == "calendar"


def test_band_calibration_uses_the_models_own_exclusions():
    """A category the model excludes is excluded from the folds' target too: it must not show up as bias."""
    base = _flat_history("2025-01-01", "2025-12-31", wobble=200.0)
    steady = pd.DataFrame({"booking_date": pd.date_range("2025-01-01", "2025-12-31"), "amount": 500.0,
                           "category": "side_flow", "team_id": None})
    with_flow = pd.concat([base, steady], ignore_index=True)
    excluded = (*m.EXCLUDED_CATEGORIES, "side_flow")
    custom = m.train(with_flow, exclude_categories=excluded, feature_set="calendar", log_to_mlflow=False)
    plain = m.train(base, feature_set="calendar", log_to_mlflow=False)
    assert custom.band_spec is not None and plain.band_spec is not None
    for key in ("p", "lower", "upper", "horizon_measured", "n_origins"):
        assert custom.band_spec[key] == pytest.approx(plain.band_spec[key], rel=1e-6), key
    assert custom.band(90) == pytest.approx(plain.band(90))


# -- the API ----------------------------------------------------------------------------------------------
@pytest.fixture
def served(monkeypatch):
    history = _flat_history("2025-01-01", "2025-12-31", wobble=200.0)
    model = _model(history)

    monkeypatch.setattr(services, "_transactions", lambda repo, **kw: history)
    monkeypatch.setattr(services, "forecast_model", lambda tx: (model, None))
    return model


def test_api_schema_is_unchanged():
    assert set(ForecastPoint.model_fields) == {"date", "balance", "lower", "upper"}


def test_api_uses_the_model_band_and_brackets_the_balance(served):
    f = services.liquidity_forecast(None, 120, "day")
    assert len(f.points) == 120
    last = pd.Timestamp(f.as_of)
    for p in f.points:
        n = (pd.Timestamp(p.date) - last).days
        lower, upper = served.band(n)
        balance = float(p.balance.amount)
        assert float(p.lower.amount) == pytest.approx(round(balance + lower, 2), abs=0.011)
        assert float(p.upper.amount) == pytest.approx(round(balance + upper, 2), abs=0.011)
        assert float(p.lower.amount) <= balance <= float(p.upper.amount)
    widths = [float(p.upper.amount) - float(p.lower.amount) for p in f.points]
    assert all(b >= a - 0.02 for a, b in zip(widths, widths[1:]))


def test_api_with_an_old_model_draws_the_old_band(served, monkeypatch):
    served.band_spec = None
    f = services.liquidity_forecast(None, 30, "day")
    p = f.points[-1]
    expected = LEGACY_Z * served.metrics["holdout_rmse"] * math.sqrt(30)
    assert float(p.upper.amount) - float(p.balance.amount) == pytest.approx(expected, abs=0.02)
    assert isinstance(p.date, date)


# -- the organizer's statement ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def statement_folds() -> pd.DataFrame:
    try:
        tx = ev.load_statement()
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    return ev.rolling_origin(tx, ev.default_trainer)


def _coverage(folds: pd.DataFrame, spec: dict, horizon: int) -> float:
    """Share of (origin, day) points with day <= `horizon` whose true balance lies inside the band.
    The truth relative to the forecast balance is `-cum_error` (error = predicted - actual)."""
    part = folds[folds["horizon"] <= horizon]
    low_high = np.array([fb.offsets(spec, int(h)) for h in part["horizon"]])
    truth = -part["cum_error"].to_numpy()
    return float(((truth >= low_high[:, 0]) & (truth <= low_high[:, 1])).mean())


@pytest.mark.parametrize("horizon", [30, 60])
def test_statement_coverage_of_the_80_percent_band(statement_folds, horizon):
    spec = fb.fit_band(statement_folds)
    assert 0.70 <= _coverage(statement_folds, spec, horizon) <= 0.95


def test_statement_band_is_wider_than_the_sqrt_band_where_it_matters(statement_folds):
    spec = fb.fit_band(statement_folds)
    rmse = float(np.sqrt((statement_folds["error"] ** 2).mean()))
    for days in (30, 60, 90, 180, 240):
        lower, upper = fb.offsets(spec, days)
        assert upper - lower > 2 * LEGACY_Z * rmse * math.sqrt(days)


def test_statement_band_is_stored_by_train():
    try:
        tx = ev.load_statement()
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    model = m.train(tx[tx["booking_date"] <= "2025-11-30"], log_to_mlflow=False)
    assert model.band_spec and model.band_spec["n_origins"] >= 4
