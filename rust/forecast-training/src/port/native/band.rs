//! Runtime rolling-origin uncertainty measurement; training CLI orchestration is retained separately.
use super::*;
#[derive(Clone)]
pub struct Fold {
    pub origin: NaiveDate,
    pub horizon: i64,
    pub cumulative_error: f64,
}
fn rounded(v: f64, n: usize) -> Result<f64> {
    format!("{v:.n$}").parse().map_err(err)
}
fn quantile(sorted: &[f64], q: f64) -> f64 {
    let at = (sorted.len() - 1) as f64 * q;
    let low = at.floor() as usize;
    let high = (low + 1).min(sorted.len() - 1);
    let t = at - low as f64;
    let (a, b) = (sorted[low], sorted[high]);
    if t < 0.5 {
        a + (b - a) * t
    } else {
        b - (b - a) * (1. - t)
    }
}
fn kahan_mean(values: &[f64]) -> f64 {
    let (mut sum, mut compensation) = (0., 0.);
    for v in values {
        let y = v - compensation;
        let next = sum + y;
        compensation = (next - sum) - y;
        sum = next;
    }
    sum / values.len() as f64
}
pub fn fit_band(folds: &[Fold]) -> Result<Option<Value>> {
    let count = folds
        .iter()
        .map(|f| f.origin)
        .collect::<BTreeSet<_>>()
        .len();
    if count < 2 {
        return Ok(None);
    }
    let mut reaching: BTreeMap<i64, BTreeSet<NaiveDate>> = BTreeMap::new();
    for row in folds {
        reaching.entry(row.horizon).or_default().insert(row.origin);
    }
    let reach = reaching
        .iter()
        .filter(|(_, origins)| origins.len() >= 2)
        .map(|(h, _)| *h)
        .max()
        .unwrap_or(0);
    if reach < 1 {
        return Ok(None);
    }
    let pool = folds
        .iter()
        .filter(|f| f.horizon <= reach)
        .collect::<Vec<_>>();
    let mut groups: BTreeMap<i64, Vec<f64>> = BTreeMap::new();
    for f in &pool {
        groups
            .entry(f.horizon)
            .or_default()
            .push(f.cumulative_error.abs());
    }
    let fit = groups
        .iter()
        .map(|(h, values)| (*h, kahan_mean(values)))
        .filter(|(h, v)| *h >= 7.min(reach) && *v > 1e-9)
        .collect::<Vec<_>>();
    let p = if fit.len() >= 2 {
        let x = fit
            .iter()
            .map(|(h, _)| (*h as f64).ln())
            .collect::<Vec<_>>();
        let y = fit.iter().map(|(_, v)| v.ln()).collect::<Vec<_>>();
        let (mx, my) = (mean(&x), mean(&y));
        let xy = x
            .iter()
            .zip(&y)
            .map(|(a, b)| (a - mx) * (b - my))
            .collect::<Vec<_>>();
        let xx = x.iter().map(|v| (v - mx) * (v - mx)).collect::<Vec<_>>();
        (kernel::pandas_sum(&xy) / kernel::pandas_sum(&xx)).clamp(0.5, 1.)
    } else {
        1.
    };
    let mut z = pool
        .iter()
        .map(|f| -f.cumulative_error / (f.horizon as f64).powf(p))
        .collect::<Vec<_>>();
    z.sort_by(f64::total_cmp);
    let inflation = (1. + 1. / count as f64).sqrt();
    let tail = (1. - 0.8) / 2.;
    let low = quantile(&z, tail);
    let high = quantile(&z, 1. - tail);
    let mut spec = json!({"method":"pooled-power-growth","level":0.8,"p":rounded(p,4)?,"lower":rounded((-low).max(0.)*inflation,4)?,"upper":rounded(high.max(0.)*inflation,4)?,"horizon_measured":reach,"n_origins":count,"inflation":rounded(inflation,4)?});
    let mut anchors = Map::new();
    for h in [7, 14, 30, 60, 90] {
        let (a, b) = kernel::band_offsets(&spec, h)?;
        anchors.insert(h.to_string(), json!([rounded(a, 2)?, rounded(b, 2)?]));
    }
    spec["anchors"] = Value::Object(anchors);
    Ok(Some(spec))
}
fn month_end(year: i32, month: u32) -> Result<NaiveDate> {
    let next = if month == 12 {
        NaiveDate::from_ymd_opt(year + 1, 1, 1)
    } else {
        NaiveDate::from_ymd_opt(year, month + 1, 1)
    };
    next.and_then(|d| d.checked_sub_signed(Duration::days(1)))
        .ok_or_else(|| "month end".into())
}
pub fn measure_band(
    tx: &[kernel::Transaction],
    params: &Map<String, Value>,
    excluded: &BTreeSet<String>,
    name: &str,
) -> Result<Option<Value>> {
    let first = tx.iter().map(|r| r.day).min().ok_or("empty history")?;
    let last = tx.iter().map(|r| r.day).max().ok_or("empty history")?;
    let month = first.year() * 12 + first.month0() as i32 + 5;
    let mut origin = month_end(month / 12, month.rem_euclid(12) as u32 + 1)?;
    let mut origins = Vec::new();
    while (last - origin).num_days() >= 30 {
        origins.push(origin);
        let next = origin + Duration::days(1);
        origin = month_end(next.year(), next.month())?;
    }
    if origins.is_empty() {
        return Ok(None);
    }
    let truth = Table::parse(kernel::daily_cash_flow(tx, None, None, excluded)?)?;
    let net = truth.col("net_cash_flow")?;
    let truth = truth
        .dates
        .iter()
        .copied()
        .zip(truth.rows.iter().map(|r| r[net]))
        .collect::<BTreeMap<_, _>>();
    let options = TrainingOptions {
        params: params.clone(),
        holdout_days: 0,
        excluded: excluded.iter().cloned().collect(),
        feature_set: Some(name.into()),
        band: false,
    };
    let mut folds = Vec::new();
    for origin in origins {
        let seen = tx
            .iter()
            .filter(|r| r.day <= origin)
            .cloned()
            .collect::<Vec<_>>();
        let model = match train(&seen, &options) {
            Ok(m) => m.model,
            Err(_) => return Ok(None),
        };
        let dates = date_range(
            origin + Duration::days(1),
            (origin + Duration::days(90)).min(last),
        );
        let predicted = model.predict(&dates)?;
        let mut cumulative = 0.;
        for (i, (d, p)) in dates.iter().zip(predicted).enumerate() {
            cumulative += p - truth.get(d).copied().unwrap_or(0.);
            folds.push(Fold {
                origin,
                horizon: i as i64 + 1,
                cumulative_error: cumulative,
            });
        }
    }
    fit_band(&folds)
}
