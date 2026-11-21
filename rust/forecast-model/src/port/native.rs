//! Portable XGBoost model state and native prediction/explanation.
//! Native libxgboost is loaded through xgb's safe API; Python is never launched.
use chrono::{Datelike, Duration, NaiveDate};
use scout_forecast_kernel as kernel;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    sync::{Arc, Mutex},
};
use xgb::{Booster, DMatrix};
pub type Result<T> = std::result::Result<T, String>;
fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn f64_value(value: &Value) -> Result<f64> {
    match value {
        Value::Number(n) => n.as_f64().ok_or_else(|| "invalid number".into()),
        Value::String(s) => s.parse().map_err(error),
        Value::Null => Ok(f64::NAN),
        Value::Object(m) if m.contains_key("$nan") => Ok(f64::NAN),
        _ => Err("invalid number".into()),
    }
}
fn value(value: f64) -> Value {
    if value.is_nan() {
        json!({"$nan":true})
    } else {
        json!(value)
    }
}
fn round(value: f64, places: usize) -> Result<f64> {
    format!("{value:.places$}").parse().map_err(error)
}
fn day(value: &str) -> Result<NaiveDate> {
    NaiveDate::parse_from_str(value.get(..10).ok_or("invalid date")?, "%Y-%m-%d").map_err(error)
}
fn string_list(value: &Value) -> Result<Vec<String>> {
    value
        .as_array()
        .ok_or("invalid list")?
        .iter()
        .map(|v| {
            v.as_str()
                .map(str::to_string)
                .ok_or_else(|| "invalid string".into())
        })
        .collect()
}
fn range(start: NaiveDate, end: NaiveDate) -> Vec<NaiveDate> {
    (0..=(end - start).num_days())
        .filter_map(|i| start.checked_add_signed(Duration::days(i)))
        .collect()
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelMeta {
    pub shutdown_days: Vec<(u32, u32)>,
    pub due_days: kernel::DueDays,
    pub trained_until: NaiveDate,
    pub params: Map<String, Value>,
    pub metrics: Map<String, Value>,
    pub excluded_categories: Vec<String>,
    pub feature_columns: Vec<String>,
    #[serde(default = "calendar")]
    pub feature_set: String,
    #[serde(default)]
    pub level_values: BTreeMap<String, f64>,
    #[serde(default)]
    pub baseline_mean: f64,
    #[serde(default, rename = "band")]
    pub band_spec: Option<Value>,
    #[serde(default)]
    pub schedule: Option<Value>,
}
fn calendar() -> String {
    "calendar".into()
}
#[derive(Clone)]
pub struct ForecastModel {
    booster: Arc<Mutex<Booster>>,
    pub meta: ModelMeta,
    pub mlflow_run_id: Option<String>,
    pub model_version: Option<String>,
    pub trained_at: Option<String>,
}
#[derive(Debug, Clone)]
pub struct NumericFrame {
    pub dates: Vec<NaiveDate>,
    pub columns: Vec<String>,
    pub rows: Vec<Vec<f64>>,
    pub float32_columns: Vec<bool>,
    pub integral_columns: Vec<bool>,
}
impl NumericFrame {
    pub fn json(&self) -> Value {
        json!({"columns":self.columns,"index":self.dates.iter().map(ToString::to_string).collect::<Vec<_>>(),"rows":self.rows.iter().map(|r|r.iter().enumerate().map(|(i,v)|if self.integral_columns[i] { json!(*v as i64) } else { value(*v) }).collect::<Vec<_>>()).collect::<Vec<_>>()})
    }
    fn column(&self, name: &str) -> Result<usize> {
        self.columns
            .iter()
            .position(|c| c == name)
            .ok_or_else(|| format!("missing feature {name}"))
    }
    fn sum_column(&self, index: usize) -> f64 {
        let values = self.rows.iter().map(|r| r[index]).collect::<Vec<_>>();
        if self.float32_columns[index] {
            sum_f32(&values.iter().map(|v| *v as f32).collect::<Vec<_>>()) as f64
        } else {
            kernel::pandas_sum(&values)
        }
    }
}
fn sum_f32(values: &[f32]) -> f32 {
    fn pairwise(values: &[f32]) -> f32 {
        let n = values.len();
        if n < 8 {
            return values.iter().fold(-0.0, |sum, v| sum + v);
        }
        if n > 128 {
            let half = n / 2 / 8 * 8;
            return pairwise(&values[..half]) + pairwise(&values[half..]);
        }
        let mut lanes = [0.0; 8];
        lanes.copy_from_slice(&values[..8]);
        let stop = n / 8 * 8;
        for block in values[8..stop].as_chunks::<8>().0 {
            for (lane, v) in lanes.iter_mut().zip(block) {
                *lane += v;
            }
        }
        let sum = ((lanes[0] + lanes[1]) + (lanes[2] + lanes[3]))
            + ((lanes[4] + lanes[5]) + (lanes[6] + lanes[7]));
        values[stop..].iter().fold(sum, |s, v| s + v)
    }
    0.0 + pairwise(values)
}

// Scalar translation of Intel x86-simd-sort's AVX2 four-lane argsort network.
// Source: fa944efbea1a33426b3f8a9a21e23794ab8aa300, BSD-3-Clause;
// see THIRD-PARTY-NOTICES. Ties retain the original lane during cmp_merge,
// which differs from stable sorting and from a textbook scalar bitonic swap.
fn numpy_argsort(values: &[f64]) -> Vec<usize> {
    if values.windows(2).all(|v| v[0] <= v[1]) {
        return (0..values.len()).collect();
    }
    type Lane = (f64, usize);
    fn merge(reg: [Lane; 4], reverse: bool, span: usize, mask: u8) -> [Lane; 4] {
        std::array::from_fn(|i| {
            let j = if reverse {
                i / span * span + span - 1 - i % span
            } else {
                i ^ (span / 2)
            };
            let (a, b) = (reg[i], reg[j]);
            let key = if mask & (1 << i) != 0 {
                a.0.max(b.0)
            } else {
                a.0.min(b.0)
            };
            if key == a.0 { a } else { b }
        })
    }
    fn coex(regs: &mut [[Lane; 4]], i: usize, j: usize, reverse: bool) {
        let a = regs[i];
        let mut b = regs[j];
        if reverse {
            b.reverse();
        }
        for lane in 0..4 {
            if a[lane].0 <= b[lane].0 {
                regs[i][lane] = a[lane];
                regs[j][if reverse { 3 - lane } else { lane }] = b[lane];
            } else {
                regs[i][lane] = b[lane];
                regs[j][if reverse { 3 - lane } else { lane }] = a[lane];
            }
        }
    }
    let size = values.len().next_power_of_two().max(4);
    let mut regs: Vec<[Lane; 4]> = (0..size / 4)
        .map(|r| {
            std::array::from_fn(|i| {
                let index = r * 4 + i;
                if index < values.len() {
                    (values[index], index)
                } else {
                    (f64::INFINITY, usize::MAX)
                }
            })
        })
        .collect();
    for reg in &mut regs {
        *reg = merge(
            merge(merge(*reg, true, 2, 0xA), true, 4, 0xC),
            false,
            2,
            0xA,
        );
    }
    let mut width = 2;
    while width <= regs.len() {
        for start in (0..regs.len()).step_by(width) {
            for i in 0..width / 2 {
                coex(&mut regs, start + i, start + width - i - 1, true);
            }
            let mut part = width / 2;
            while part >= 2 {
                for j in (0..width).step_by(part) {
                    for i in 0..part / 2 {
                        coex(&mut regs, start + j + i, start + j + i + part / 2, false);
                    }
                }
                part /= 2;
            }
            for reg in &mut regs[start..start + width] {
                *reg = merge(merge(*reg, false, 4, 0xC), false, 2, 0xA);
            }
        }
        width *= 2;
    }
    regs.into_iter()
        .flatten()
        .map(|(_, i)| i)
        .take(values.len())
        .collect()
}
fn pandas_desc_indices(values: &[f64]) -> Vec<usize> {
    let reversed: Vec<_> = values.iter().rev().copied().collect();
    numpy_argsort(&reversed)
        .into_iter()
        .rev()
        .map(|i| values.len() - 1 - i)
        .collect()
}

impl ForecastModel {
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let bytes = std::fs::read(path).map_err(error)?;
        let metadata =
            std::fs::read(format!("{}.meta.json", path.to_string_lossy())).map_err(error)?;
        Self::from_bytes(&bytes, serde_json::from_slice(&metadata).map_err(error)?)
    }
    pub fn from_bytes(bytes: &[u8], mut metadata: Value) -> Result<Self> {
        xgb::library_available()?;
        if metadata["feature_set"].as_str().is_none_or(str::is_empty) {
            metadata["feature_set"] = json!("calendar");
        }
        for field in ["band", "schedule"] {
            if metadata[field].is_null() || metadata[field].as_object().is_some_and(Map::is_empty) {
                metadata[field] = Value::Null;
            }
        }
        if metadata["baseline_mean"].is_null() {
            metadata["baseline_mean"] = json!(0.0);
        }
        if metadata["level_values"].is_null() {
            metadata["level_values"] = json!({});
        }
        let meta: ModelMeta = serde_json::from_value(metadata).map_err(error)?;
        let booster = Booster::load_buffer(bytes).map_err(error)?;
        Ok(Self {
            booster: Arc::new(Mutex::new(booster)),
            meta,
            mlflow_run_id: None,
            model_version: None,
            trained_at: None,
        })
    }
    /// Portable JSON booster bytes, with no Python or process-bound pickle state.
    pub fn model_bytes(&self) -> Result<Vec<u8>> {
        self.booster
            .lock()
            .map_err(error)?
            .save_buffer(false)
            .map_err(error)
    }
    pub fn save(&self, path: impl AsRef<Path>) -> Result<()> {
        let path = path.as_ref();
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent).map_err(error)?;
        }
        std::fs::write(path, self.model_bytes()?).map_err(error)?;
        std::fs::write(
            format!("{}.meta.json", path.to_string_lossy()),
            serde_json::to_vec_pretty(&self.metadata()?).map_err(error)?,
        )
        .map_err(error)
    }
    pub fn metadata(&self) -> Result<Value> {
        serde_json::to_value(&self.meta).map_err(error)
    }
    fn full_frame(&self, dates: &[NaiveDate]) -> Result<NumericFrame> {
        let shutdown = self
            .meta
            .shutdown_days
            .iter()
            .copied()
            .collect::<BTreeSet<_>>();
        let frame = kernel::calendar_features(dates, &shutdown, &self.meta.due_days)?;
        let mut columns = string_list(&frame["columns"])?;
        let mut rows = frame["rows"]
            .as_array()
            .ok_or("missing rows")?
            .iter()
            .map(|r| {
                r.as_array()
                    .ok_or_else(|| "missing row".into())
                    .and_then(|r| r.iter().map(f64_value).collect())
            })
            .collect::<Result<Vec<Vec<f64>>>>()?;
        for (name, &number) in &self.meta.level_values {
            if let Some(index) = columns.iter().position(|c| c == name) {
                for row in &mut rows {
                    row[index] = number;
                }
            } else {
                columns.push(name.clone());
                for row in &mut rows {
                    row.push(number);
                }
            }
        }
        let float32_columns = vec![false; columns.len()];
        let integral_columns = columns
            .iter()
            .map(|name| !self.meta.level_values.contains_key(name))
            .collect();
        Ok(NumericFrame {
            dates: dates.into(),
            columns,
            rows,
            float32_columns,
            integral_columns,
        })
    }
    pub fn features_for(&self, dates: &[NaiveDate]) -> Result<NumericFrame> {
        let frame = self.full_frame(dates)?;
        let indices = self
            .meta
            .feature_columns
            .iter()
            .map(|c| frame.column(c))
            .collect::<Result<Vec<_>>>()?;
        Ok(NumericFrame {
            dates: dates.into(),
            columns: self.meta.feature_columns.clone(),
            rows: frame
                .rows
                .iter()
                .map(|r| indices.iter().map(|i| r[*i]).collect())
                .collect(),
            float32_columns: vec![false; indices.len()],
            integral_columns: indices.iter().map(|i| frame.integral_columns[*i]).collect(),
        })
    }
    fn matrix(&self, frame: &NumericFrame) -> Result<DMatrix> {
        let flattened = frame
            .rows
            .iter()
            .flat_map(|r| r.iter().map(|v| *v as f32))
            .collect::<Vec<_>>();
        let mut matrix = DMatrix::from_dense_with_missing(&flattened, frame.rows.len(), f32::NAN)
            .map_err(error)?;
        let names = frame.columns.iter().map(String::as_str).collect::<Vec<_>>();
        matrix.set_feature_names(&names).map_err(error)?;
        Ok(matrix)
    }
    fn levels(&self) -> Result<f64> {
        let v = &self.meta.level_values;
        let get = |name: &str| {
            v.get(name)
                .copied()
                .ok_or_else(|| format!("missing level {name}"))
        };
        let revenue = (get("recent_revenue_28d")? + get("recent_revenue_90d")?) / 2.0;
        let cost = (get("recent_variable_cost_28d")? + get("recent_variable_cost_90d")?) / 2.0;
        let value = revenue - cost;
        Ok(if value.is_nan() { 0.0 } else { value })
    }
    pub fn with_levels_from(&self, tx: &[kernel::Transaction]) -> Result<Self> {
        if self.meta.level_values.is_empty() || tx.is_empty() {
            return Ok(self.clone());
        }
        let origin = tx.iter().map(|r| r.day).max().ok_or("no transaction")? + Duration::days(1);
        let fresh = kernel::recent_levels(tx, &[origin], &[28, 90])?;
        let columns = string_list(&fresh["columns"])?;
        let values = fresh["rows"][0]
            .as_array()
            .ok_or("missing levels")?
            .iter()
            .map(f64_value)
            .collect::<Result<Vec<_>>>()?;
        if values.iter().any(|v| v.is_nan()) {
            return Ok(self.clone());
        }
        let mut updated = self.clone();
        updated.meta.level_values = columns.into_iter().zip(values).collect();
        if self.meta.schedule.is_some() {
            let categories = kernel::FIXED_CATEGORIES
                .into_iter()
                .filter(|c| {
                    !self
                        .meta
                        .excluded_categories
                        .iter()
                        .any(|excluded| excluded == c)
                })
                .map(str::to_string)
                .collect::<Vec<_>>();
            let schedule = kernel::infer_schedule(tx, &categories)?;
            if schedule["categories"]
                .as_object()
                .is_some_and(|v| !v.is_empty())
            {
                updated.meta.schedule = Some(schedule);
            }
        }
        Ok(updated)
    }
    pub fn predict(&self, dates: &[NaiveDate]) -> Result<Vec<f64>> {
        if dates.is_empty() {
            return Err("invalid model input".into());
        }
        let features = self.features_for(dates)?;
        let matrix = self.matrix(&features)?;
        let raw = self
            .booster
            .lock()
            .map_err(error)?
            .predict(&matrix)
            .map_err(error)?;
        let full = self.full_frame(dates)?;
        let shutdown = full.column("shutdown_flag")?;
        let component = self.meta.feature_set == "components";
        let offset = matches!(
            self.meta.feature_set.as_str(),
            "components" | "level_baseline"
        );
        let baseline = if offset { self.levels()? } else { 0.0 };
        let schedule = if component {
            self.meta
                .schedule
                .as_ref()
                .map(|s| kernel::scheduled_amounts(s, dates, true))
                .transpose()?
        } else {
            None
        };
        raw.into_iter()
            .enumerate()
            .map(|(i, v)| {
                let closed = full.rows[i][shutdown];
                let mut prediction = v as f64;
                if component {
                    prediction *= 1.0 - closed;
                }
                if offset {
                    prediction += baseline * (1.0 - closed);
                }
                if let Some(schedule) = &schedule {
                    prediction += f64_value(&schedule["values"][i])?;
                }
                Ok(prediction)
            })
            .collect()
    }
    pub fn band(&self, days: i64) -> Result<(f64, f64)> {
        if let Some(spec) = &self.meta.band_spec {
            return kernel::band_offsets(spec, days);
        }
        let sigma = self
            .meta
            .metrics
            .get("holdout_rmse")
            .map(f64_value)
            .transpose()?
            .filter(|v| *v != 0.0)
            .unwrap_or(
                self.meta
                    .metrics
                    .get("train_mae")
                    .map(f64_value)
                    .transpose()?
                    .unwrap_or(0.0)
                    * 1.25,
            );
        Ok(kernel::legacy_offsets(sigma, days))
    }
    pub fn feature_importances(&self) -> Result<Vec<(String, f64)>> {
        let bytes = self
            .booster
            .lock()
            .map_err(error)?
            .save_buffer(false)
            .map_err(error)?;
        let model: Value = serde_json::from_slice(&bytes).map_err(error)?;
        let trees = model["learner"]["gradient_booster"]["model"]["trees"]
            .as_array()
            .ok_or("unsupported booster model")?;
        let mut gain = vec![0.0_f32; self.meta.feature_columns.len()];
        for tree in trees {
            let mut stack = vec![0_usize];
            while let Some(node) = stack.pop() {
                let left = tree["left_children"][node]
                    .as_i64()
                    .ok_or("missing tree child")?;
                if left < 0 {
                    continue;
                }
                let right = tree["right_children"][node]
                    .as_i64()
                    .ok_or("missing tree child")?;
                let feature = tree["split_indices"][node]
                    .as_u64()
                    .ok_or("missing split")? as usize;
                *gain.get_mut(feature).ok_or("invalid split index")? +=
                    f64_value(&tree["loss_changes"][node])? as f32;
                stack.push(left as usize);
                stack.push(right as usize);
            }
        }
        let total = kernel::pandas_sum(&gain.iter().map(|v| *v as f64).collect::<Vec<_>>());
        let result = self
            .meta
            .feature_columns
            .iter()
            .cloned()
            .zip(gain.into_iter().map(|v| {
                if total != 0.0 {
                    v as f64 / total
                } else {
                    v as f64
                }
            }))
            .collect::<Vec<_>>();
        Ok(
            pandas_desc_indices(&result.iter().map(|v| v.1).collect::<Vec<_>>())
                .into_iter()
                .map(|i| result[i].clone())
                .collect(),
        )
    }
    pub fn contributions(&self, dates: &[NaiveDate]) -> Result<NumericFrame> {
        let full = self.full_frame(dates)?;
        let selected = self.features_for(dates)?;
        let mut columns = self.meta.feature_columns.clone();
        columns.push("bias".into());
        let mut flags = vec![true; columns.len()];
        let mut integral = vec![false; columns.len()];
        let raw = if dates.is_empty() {
            Vec::new()
        } else {
            let matrix = self.matrix(&selected)?;
            self.booster
                .lock()
                .map_err(error)?
                .predict_contributions(&matrix)
                .map_err(error)?
                .0
        };
        let mut rows = raw
            .chunks(columns.len())
            .map(|r| r.iter().map(|v| *v as f64).collect::<Vec<_>>())
            .collect::<Vec<_>>();
        let component = self.meta.feature_set == "components";
        let offset = matches!(
            self.meta.feature_set.as_str(),
            "components" | "level_baseline"
        );
        let shutdown_column = if component || offset {
            Some(
                columns
                    .iter()
                    .position(|c| c == "shutdown_flag")
                    .ok_or("missing shutdown feature")?,
            )
        } else {
            None
        };
        let full_shutdown = full.column("shutdown_flag")?;
        let bias = columns.len() - 1;
        if component {
            let index = shutdown_column.ok_or("shutdown")?;
            flags[index] = false;
            for (i, row) in rows.iter_mut().enumerate() {
                let total = sum_f32(&row.iter().map(|v| *v as f32).collect::<Vec<_>>()) as f64;
                row[index] -= total * full.rows[i][full_shutdown];
            }
        }
        if offset {
            let index = shutdown_column.ok_or("shutdown")?;
            let level = self.levels()?;
            flags[index] = false;
            for (i, row) in rows.iter_mut().enumerate() {
                row[bias] = ((row[bias] as f32) + (self.meta.baseline_mean as f32)) as f64;
                row[index] -= level * full.rows[i][full_shutdown];
                row.insert(bias, level - self.meta.baseline_mean);
            }
            columns.insert(bias, "recent_level_baseline".into());
            flags.insert(bias, false);
            integral.insert(bias, false);
        }
        if component && let Some(spec) = &self.meta.schedule {
            let scheduled = kernel::scheduled_amounts(spec, dates, false)?;
            let names = string_list(&scheduled["columns"])?;
            for (j, name) in names.iter().enumerate() {
                columns.push(format!("scheduled_{name}"));
                flags.push(false);
                integral.push(
                    !dates.is_empty()
                        && scheduled["rows"].as_array().is_some_and(|rows| {
                            rows.iter()
                                .all(|r| r[j].as_number().is_some_and(|n| n.is_i64()))
                        }),
                );
                for (i, row) in rows.iter_mut().enumerate() {
                    row.push(f64_value(&scheduled["rows"][i][j])?);
                }
            }
        }
        Ok(NumericFrame {
            dates: dates.into(),
            columns,
            rows,
            float32_columns: flags,
            integral_columns: integral,
        })
    }
    pub fn explain(&self, start: NaiveDate, end: NaiveDate, top: usize) -> Result<Value> {
        let dates = range(start, end);
        if dates.is_empty() {
            return Err("empty explanation period".into());
        }
        let contrib = self.contributions(&dates)?;
        let bias = contrib.column("bias")?;
        let sums = (0..contrib.columns.len())
            .map(|i| contrib.sum_column(i))
            .collect::<Vec<_>>();
        let drivers = contrib
            .columns
            .iter()
            .cloned()
            .zip(sums.iter().copied())
            .filter(|(name, _)| name != "bias")
            .collect::<Vec<_>>();
        let drivers = pandas_desc_indices(&drivers.iter().map(|v| v.1.abs()).collect::<Vec<_>>())
            .into_iter()
            .map(|i| drivers[i].clone())
            .take(top)
            .map(|(name, v)| Ok(json!({"feature":name,"contribution_eur":round(v,2)?})))
            .collect::<Result<Vec<_>>>()?;
        let importances = self
            .feature_importances()?
            .into_iter()
            .take(top)
            .map(|(name, v)| Ok((name, value(round(v, 4)?))))
            .collect::<Result<Map<_, _>>>()?;
        Ok(
            json!({"start":dates[0].to_string(),"end":dates[dates.len()-1].to_string(),"days":dates.len(),"predicted_net_eur":round(kernel::pandas_sum(&sums),2)?,"baseline_eur":round(sums[bias],2)?,"top_drivers":drivers,"feature_importances":importances}),
        )
    }
}
#[derive(Debug, Clone)]
pub struct Forecast {
    pub dates: Vec<NaiveDate>,
    pub predicted_net: Vec<f64>,
    pub scheduled: Vec<f64>,
    pub net: Vec<f64>,
    pub balance: Vec<f64>,
    pub start_balance: f64,
    pub last_known_date: NaiveDate,
}
impl Forecast {
    pub fn json(&self) -> Value {
        let rows = (0..self.dates.len())
            .map(|i| {
                vec![
                    value(self.predicted_net[i]),
                    value(self.scheduled[i]),
                    value(self.net[i]),
                    value(self.balance[i]),
                ]
            })
            .collect::<Vec<_>>();
        json!({"frame":{"columns":["predicted_net","scheduled","net","balance"],"index":self.dates.iter().map(ToString::to_string).collect::<Vec<_>>(),"rows":rows},"attrs":{"start_balance":self.start_balance,"last_known_date":self.last_known_date.to_string()}})
    }
    pub fn lowest_balance(&self) -> Result<(NaiveDate, f64)> {
        let mut best = None;
        for (i, &balance) in self.balance.iter().enumerate() {
            if balance.is_nan() {
                continue;
            }
            if best.is_none_or(|(_, low)| balance < low) {
                best = Some((i, balance));
            }
        }
        best.map(|(i, value)| (self.dates[i], value))
            .ok_or_else(|| "no balances".into())
    }
}
pub fn last_known_balance(tx: &[kernel::Transaction]) -> Result<(NaiveDate, f64)> {
    let last = tx
        .iter()
        .map(|r| r.day)
        .max()
        .ok_or("no transactions: nothing to forecast from")?;
    let balance = round(
        kernel::pandas_sum(&tx.iter().map(|r| r.amount).collect::<Vec<_>>()),
        2,
    )?;
    Ok((last, balance))
}
fn add_months(day: NaiveDate, months: i32) -> Result<NaiveDate> {
    let index = day.year() * 12 + day.month0() as i32 + months;
    let year = index.div_euclid(12);
    let month = (index.rem_euclid(12) + 1) as u32;
    let first = NaiveDate::from_ymd_opt(year, month, 1).ok_or("invalid month")?;
    let next = if month == 12 {
        NaiveDate::from_ymd_opt(year + 1, 1, 1)
    } else {
        NaiveDate::from_ymd_opt(year, month + 1, 1)
    };
    let last = next.and_then(|v| v.pred_opt()).ok_or("invalid month")?;
    first
        .with_day(day.day().min(last.day()))
        .ok_or_else(|| "invalid month".into())
}
pub fn partner_tranche_schedule(
    tx: &[kernel::Transaction],
    total: usize,
) -> Result<BTreeMap<NaiveDate, f64>> {
    let mut rows = tx
        .iter()
        .filter(|r| r.category.as_deref() == Some("partner_investment"))
        .collect::<Vec<_>>();
    let remaining = total.saturating_sub(rows.len());
    if remaining == 0 || rows.is_empty() {
        return Ok(BTreeMap::new());
    }
    rows.sort_by_key(|r| r.day);
    let last = rows.last().ok_or("no partner transactions")?;
    (1..=remaining)
        .map(|i| Ok((add_months(last.day, i as i32)?, last.amount)))
        .collect()
}
pub fn liquidity_forecast(
    model: &ForecastModel,
    tx: &[kernel::Transaction],
    days: usize,
    start_balance: Option<f64>,
    scheduled: Option<&BTreeMap<NaiveDate, f64>>,
) -> Result<Forecast> {
    if days < 1 {
        return Err("days must be >= 1".into());
    }
    if tx.is_empty() {
        return Err("no transactions: nothing to forecast from".into());
    }
    let model = model.with_levels_from(tx)?;
    let (last, known) = last_known_balance(tx)?;
    let start = start_balance.unwrap_or(known);
    let dates = (1..=days)
        .map(|i| {
            last.checked_add_signed(Duration::days(i as i64))
                .ok_or_else(|| "invalid forecast date".into())
        })
        .collect::<Result<Vec<_>>>()?;
    let predicted_net = model.predict(&dates)?;
    let default = partner_tranche_schedule(tx, 12)?;
    let schedule = scheduled.unwrap_or(&default);
    let scheduled = dates
        .iter()
        .map(|d| schedule.get(d).copied().unwrap_or(0.0))
        .collect::<Vec<_>>();
    let net = predicted_net
        .iter()
        .zip(&scheduled)
        .map(|(a, b)| a + b)
        .collect::<Vec<_>>();
    let mut cumulative = 0.0;
    let balance = net
        .iter()
        .map(|v| {
            cumulative += v;
            start + cumulative
        })
        .collect();
    Ok(Forecast {
        dates,
        predicted_net,
        scheduled,
        net,
        balance,
        start_balance: start,
        last_known_date: last,
    })
}
fn transactions(body: &Value) -> Result<Vec<kernel::Transaction>> {
    let empty = Vec::new();
    let rows = body
        .get("transactions")
        .and_then(Value::as_array)
        .unwrap_or(&empty);
    rows.iter()
        .map(|r| {
            Ok(kernel::Transaction {
                day: day(r["booking_date"].as_str().ok_or("missing booking date")?)?,
                amount: f64_value(&r["amount"])?,
                category: r["category"].as_str().map(str::to_string),
            })
        })
        .collect()
}
pub fn dispatch(body: &Value) -> Result<Option<Value>> {
    let op = body["op"].as_str().unwrap_or_default();
    if ![
        "meta",
        "features",
        "predict",
        "band",
        "contributions",
        "importance",
        "explain",
        "forecast",
        "lowest",
        "levels",
        "partner_schedule",
        "last_balance",
    ]
    .contains(&op)
    {
        return Ok(None);
    }
    let tx = transactions(body)?;
    if op == "partner_schedule" {
        let total = body.get("total").and_then(Value::as_u64).unwrap_or(12);
        let schedule = partner_tranche_schedule(&tx, total as usize)?;
        return Ok(Some(Value::Object(
            schedule
                .into_iter()
                .map(|(d, a)| (d.to_string(), value(a)))
                .collect(),
        )));
    }
    if op == "last_balance" {
        let (date, balance) = last_known_balance(&tx)?;
        return Ok(Some(json!({"date":date.to_string(),"balance":balance})));
    }
    let model_file = body["model"].as_str().ok_or("missing model")?;
    if ![
        "calendar.json",
        "level.json",
        "level_baseline.json",
        "components.json",
        "legacy-meta.json",
    ]
    .contains(&model_file)
    {
        return Err("unknown fixture model".into());
    }
    let model = ForecastModel::load(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fixtures")
            .join(model_file),
    )?;
    let model = if body["refresh"] == true {
        model.with_levels_from(&tx)?
    } else {
        model
    };
    let dates = body
        .get("dates")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .map(|d| day(d.as_str().ok_or("invalid date")?))
                .collect::<Result<Vec<_>>>()
        })
        .transpose()?
        .unwrap_or_default();
    let output = match op {
        "meta" | "levels" => model.metadata()?,
        "features" => model.features_for(&dates)?.json(),
        "predict" => json!(
            model
                .predict(&dates)?
                .into_iter()
                .map(value)
                .collect::<Vec<_>>()
        ),
        "band" => {
            let (lower, upper) = model.band(body["days"].as_i64().ok_or("missing days")?)?;
            json!([value(lower), value(upper)])
        }
        "contributions" => model.contributions(&dates)?.json(),
        "importance" => json!(model.feature_importances()?),
        "explain" => model.explain(
            day(body["start"].as_str().ok_or("start")?)?,
            day(body["end"].as_str().ok_or("end")?)?,
            body.get("top").and_then(Value::as_u64).unwrap_or(5) as usize,
        )?,
        "forecast" | "lowest" => {
            let days = body["days"].as_u64().ok_or("missing days")? as usize;
            let start = body
                .get("start_balance")
                .filter(|v| !v.is_null())
                .map(f64_value)
                .transpose()?;
            let scheduled = body
                .get("scheduled")
                .filter(|v| !v.is_null())
                .map(|v| {
                    v.as_object()
                        .ok_or_else(|| "invalid schedule".into())
                        .and_then(|m| {
                            m.iter()
                                .map(|(d, a)| Ok((day(d)?, f64_value(a)?)))
                                .collect::<Result<BTreeMap<_, _>>>()
                        })
                })
                .transpose()?;
            let forecast = liquidity_forecast(&model, &tx, days, start, scheduled.as_ref())?;
            if op == "forecast" {
                forecast.json()
            } else {
                let (day, balance) = forecast.lowest_balance()?;
                json!({"date":day.to_string(),"balance":balance})
            }
        }
        _ => return Ok(None),
    };
    Ok(Some(output))
}

#[cfg(test)]
mod sorting_tests {
    use super::*;
    #[test]
    fn matches_numpy_avx2_sort_network() -> Result<()> {
        let cases: Value =
            serde_json::from_str(include_str!("../../sort-cases.json")).map_err(error)?;
        for (i, case) in cases.as_array().ok_or("cases")?.iter().enumerate() {
            let values = case["values"]
                .as_array()
                .ok_or("values")?
                .iter()
                .map(f64_value)
                .collect::<Result<Vec<_>>>()?;
            if json!(numpy_argsort(&values)) != case["indices"] {
                return Err(format!("NumPy sort case {i} differs"));
            }
        }
        Ok(())
    }
}
