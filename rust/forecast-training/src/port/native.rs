//! Tenant-local XGBoost training and chronological holdout evaluation.
use chrono::{Datelike, Duration, NaiveDate};
use scout_forecast_kernel as kernel;
use scout_forecast_model_native::{ForecastModel, ModelMeta};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
use xgb::{Booster, DMatrix};
#[path = "native/band.rs"]
pub mod band;
pub type Result<T> = std::result::Result<T, String>;
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn number(v: &Value) -> Result<f64> {
    if v.is_object() && v.get("$nan").is_some() {
        Ok(f64::NAN)
    } else {
        v.as_f64().ok_or_else(|| "number".into())
    }
}
fn date(s: &str) -> Result<NaiveDate> {
    NaiveDate::parse_from_str(s.get(..10).ok_or("date")?, "%Y-%m-%d").map_err(err)
}
fn date_range(first: NaiveDate, last: NaiveDate) -> Vec<NaiveDate> {
    (0..=(last - first).num_days())
        .map(|i| first + Duration::days(i))
        .collect()
}
fn levels(name: &str) -> bool {
    matches!(name, "level" | "level_baseline" | "components")
}
fn offsets(name: &str) -> bool {
    matches!(name, "level_baseline" | "components")
}
#[derive(Clone)]
pub struct Table {
    pub columns: Vec<String>,
    pub dates: Vec<NaiveDate>,
    pub rows: Vec<Vec<f64>>,
    pub integral: Vec<bool>,
}
impl Table {
    fn parse(v: Value) -> Result<Self> {
        let columns = v["columns"]
            .as_array()
            .ok_or("columns")?
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_string)
                    .ok_or_else(|| "column".into())
            })
            .collect::<Result<Vec<_>>>()?;
        let dates = v["index"]
            .as_array()
            .ok_or("index")?
            .iter()
            .map(|v| date(v.as_str().ok_or("date")?))
            .collect::<Result<Vec<_>>>()?;
        let a = v["rows"].as_array().ok_or("rows")?;
        let integral = (0..columns.len())
            .map(|j| a.first().is_some_and(|r| r[j].is_i64()))
            .collect();
        let rows = a
            .iter()
            .map(|r| {
                r.as_array()
                    .ok_or("row".into())
                    .and_then(|r| r.iter().map(number).collect())
            })
            .collect::<Result<_>>()?;
        Ok(Self {
            columns,
            dates,
            rows,
            integral,
        })
    }
    pub fn json(&self) -> Value {
        json!({"columns":self.columns,"index":self.dates.iter().map(ToString::to_string).collect::<Vec<_>>(),"rows":self.rows.iter().map(|r|r.iter().enumerate().map(|(j,v)|if v.is_nan(){json!({"$nan":true})}else if self.integral[j]{json!(*v as i64)}else{json!(v)}).collect::<Vec<_>>()).collect::<Vec<_>>()})
    }
    fn col(&self, name: &str) -> Result<usize> {
        self.columns
            .iter()
            .position(|c| c == name)
            .ok_or_else(|| format!("column {name}"))
    }
    fn filter(&self, keep: impl Fn(usize, &[f64]) -> bool) -> Self {
        let indices = self
            .rows
            .iter()
            .enumerate()
            .filter_map(|(i, r)| keep(i, r).then_some(i))
            .collect::<Vec<_>>();
        Self {
            columns: self.columns.clone(),
            dates: indices.iter().map(|i| self.dates[*i]).collect(),
            rows: indices.iter().map(|i| self.rows[*i].clone()).collect(),
            integral: self.integral.clone(),
        }
    }
    fn join(&mut self, other: Self) -> Result<()> {
        if self.dates != other.dates {
            return Err("index mismatch".into());
        }
        self.columns.extend(other.columns);
        self.integral.extend(other.integral);
        for (r, b) in self.rows.iter_mut().zip(other.rows) {
            r.extend(b)
        }
        Ok(())
    }
    fn select(&self, names: &[String]) -> Result<Self> {
        let positions = names
            .iter()
            .map(|n| self.col(n))
            .collect::<Result<Vec<_>>>()?;
        Ok(Self {
            columns: names.to_vec(),
            dates: self.dates.clone(),
            rows: self
                .rows
                .iter()
                .map(|r| positions.iter().map(|i| r[*i]).collect())
                .collect(),
            integral: positions.iter().map(|i| self.integral[*i]).collect(),
        })
    }
    fn baseline(&self, row: &[f64], apply: bool) -> Result<f64> {
        let get = |n: &str| self.col(n).map(|i| row[i]);
        let r = (get("recent_revenue_28d")? + get("recent_revenue_90d")?) / 2.;
        let c = (get("recent_variable_cost_28d")? + get("recent_variable_cost_90d")?) / 2.;
        let b = r - c;
        Ok(if b.is_nan() { 0. } else { b }
            * if apply {
                1. - get("shutdown_flag")?
            } else {
                1.
            })
    }
    fn target(&self, name: &str) -> Result<Vec<f64>> {
        let net = self.col("net_cash_flow")?;
        self.rows
            .iter()
            .map(|r| {
                Ok(r[net]
                    - if offsets(name) {
                        self.baseline(r, true)?
                    } else {
                        0.
                    })
            })
            .collect()
    }
    fn matrix(&self, columns: &[String], labels: Option<&[f64]>) -> Result<DMatrix> {
        let selected = self.select(columns)?;
        let dense = selected
            .rows
            .iter()
            .flatten()
            .map(|v| *v as f32)
            .collect::<Vec<_>>();
        let mut dm =
            DMatrix::from_dense_with_missing(&dense, self.rows.len(), f32::NAN).map_err(err)?;
        dm.set_feature_names(&columns.iter().map(String::as_str).collect::<Vec<_>>())
            .map_err(err)?;
        if let Some(y) = labels {
            dm.set_labels(&y.iter().map(|v| *v as f32).collect::<Vec<_>>())
                .map_err(err)?;
        }
        Ok(dm)
    }
}
pub fn training_table(
    tx: &[kernel::Transaction],
    excluded: &BTreeSet<String>,
    name: &str,
    keep_closed: bool,
) -> Result<Table> {
    let mut target_excluded = excluded.clone();
    if name == "components" {
        target_excluded.extend(kernel::FIXED_CATEGORIES.iter().map(|s| s.to_string()));
    }
    let mut table = Table::parse(kernel::daily_features(tx, None, &target_excluded, 7)?)?;
    let net = table.col("net_cash_flow")?;
    table = table.filter(|_, r| !r[net].is_nan());
    if levels(name) {
        let recent = Table::parse(kernel::recent_levels(tx, &table.dates, &[28, 90])?)?;
        table.join(recent)?;
        let positions = kernel::LEVEL_COLUMNS
            .iter()
            .map(|c| table.col(c))
            .collect::<Result<Vec<_>>>()?;
        table = table.filter(|_, r| positions.iter().all(|i| !r[*i].is_nan()));
        let shutdown = table.col("shutdown_flag")?;
        let closed = kernel::shutdown_days(tx, 7, true)?;
        for (i, r) in table.rows.iter_mut().enumerate() {
            if closed.contains(&table.dates[i]) {
                r[shutdown] = r[shutdown].max(1.);
            }
        }
    }
    if name == "components" && !keep_closed {
        let shutdown = table.col("shutdown_flag")?;
        table = table.filter(|_, r| r[shutdown] == 0.);
    }
    Ok(table)
}
pub fn backtest_split(
    tx: &[kernel::Transaction],
    holdout: usize,
    excluded: &BTreeSet<String>,
    name: &str,
) -> Result<(Table, Table)> {
    let table = training_table(tx, excluded, name, true)?;
    if holdout == 0 || holdout > table.rows.len() {
        return Err("invalid holdout".into());
    }
    let cutoff = table.rows.len() - holdout;
    let first = table.dates[cutoff];
    let seen = tx
        .iter()
        .filter(|r| r.day < first)
        .cloned()
        .collect::<Vec<_>>();
    let shutdown = kernel::shutdown_days(&seen, 7, false)?
        .iter()
        .map(|d| (d.month(), d.day()))
        .collect();
    let mut features = Table::parse(kernel::calendar_features(
        &table.dates,
        &shutdown,
        &kernel::infer_due_days(&seen, 3),
    )?)?;
    if levels(name) {
        features.join(Table::parse(kernel::recent_levels(
            &seen,
            &table.dates,
            &[28, 90],
        )?)?)?;
        let flag = features.col("shutdown_flag")?;
        let closed = kernel::shutdown_days(&seen, 7, true)?;
        for (i, r) in features.rows.iter_mut().enumerate() {
            if closed.contains(&features.dates[i]) {
                r[flag] = r[flag].max(1.);
            }
        }
    }
    let mut names = vec!["net_cash_flow".to_string()];
    names.extend(kernel::feature_set_columns(name)?);
    if levels(name) {
        for column in kernel::LEVEL_COLUMNS {
            if !names.iter().any(|n| n == column) {
                names.push(column.into());
            }
        }
    }
    let target = table.select(&["net_cash_flow".into()])?;
    let mut split = target;
    split.join(features)?;
    let split = split.select(&names)?;
    let train = split.filter(|i, _| i < cutoff);
    let train = if name == "components" {
        let flag = train.col("shutdown_flag")?;
        train.filter(|_, r| r[flag] == 0.)
    } else {
        train
    };
    let test = split.filter(|i, _| i >= cutoff);
    Ok((train, test))
}
fn defaults(name: &str, extra: &Map<String, Value>) -> Map<String, Value> {
    let mut params=json!({"n_estimators":200,"max_depth":3,"learning_rate":0.05,"min_child_weight":3,"subsample":1.0,"reg_lambda":1.0,"random_state":0,"n_jobs":1,"tree_method":"hist"}).as_object().cloned().unwrap_or_default();
    if levels(name) {
        params.insert("n_estimators".into(), json!(400));
        params.insert("learning_rate".into(), json!(0.1));
    }
    if name == "components" {
        for(k,v)in json!({"n_estimators":326,"max_depth":2,"learning_rate":0.2768,"min_child_weight":20,"subsample":0.9904,"colsample_bytree":0.7761,"reg_lambda":3.2495,"reg_alpha":0.02,"gamma":0.1091,"objective":"reg:squarederror"}).as_object().into_iter().flatten(){params.insert(k.clone(),v.clone());}
    }
    params.extend(extra.clone());
    params
}
// Empty learner saved by official XGBoost 3.4.1 with zero trees. It contains no
// fitted parameters or tenant data; boost_from_average remains enabled. xgb's
// Booster::new otherwise explicitly sets base_score=0.5 and disables this source default.
const EMPTY_LEARNER: &str = r#"{"learner":{"attributes":{},"feature_names":[],"feature_types":[],"gradient_booster":{"model":{"cats":{"enc":[],"feature_segments":[],"sorted_idx":[]},"gbtree_model_param":{"num_parallel_tree":"1","num_trees":"0"},"iteration_indptr":[0],"tree_info":[],"trees":[]},"name":"gbtree"},"learner_model_param":{"base_score":"[5E-1]","boost_from_average":"1","num_class":"0","num_feature":"0","num_target":"1"},"objective":{"name":"reg:squarederror","reg_loss_param":{"scale_pos_weight":"1"}}},"version":[3,4,1]}"#;
fn fit(
    params: &Map<String, Value>,
    table: &Table,
    target: &[f64],
    columns: &[String],
    watch: &[(&str, &Table, Vec<f64>)],
) -> Result<(Booster, BTreeMap<String, Vec<f64>>)> {
    xgb::library_available()?;
    let dm = table.matrix(columns, Some(target))?;
    let mut initial: Value = serde_json::from_str(EMPTY_LEARNER).map_err(err)?;
    initial["learner"]["learner_model_param"]["num_feature"] = json!(columns.len().to_string());
    let mut booster =
        Booster::load_buffer(&serde_json::to_vec(&initial).map_err(err)?).map_err(err)?;
    for (k, v) in params {
        if k == "n_estimators" || k == "eval_metric" || v.is_null() {
            continue;
        }
        let key = match k.as_str() {
            "n_jobs" => "nthread",
            "random_state" => "seed",
            _ => k,
        };
        booster
            .set_param(
                key,
                &v.as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| v.to_string()),
            )
            .map_err(err)?;
    }
    booster
        .set_feature_names(&columns.iter().map(String::as_str).collect())
        .map_err(err)?;
    let watchers = watch
        .iter()
        .map(|(n, t, y)| {
            Ok((
                *n,
                t.matrix(columns, Some(y))?,
                y.iter().map(|v| *v as f32).collect::<Vec<_>>(),
            ))
        })
        .collect::<Result<Vec<_>>>()?;
    let rounds = params
        .get("n_estimators")
        .and_then(Value::as_u64)
        .unwrap_or(100);
    let mut curves: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for i in 0..rounds {
        booster.update(&dm, i as i32).map_err(err)?;
        for (name, matrix, labels) in &watchers {
            let pred = booster.predict(matrix).map_err(err)?;
            let mut sq = 0.;
            let mut abs = 0.;
            for (p, y) in pred.iter().zip(labels) {
                let d = *y - *p;
                sq += (d * d) as f64;
                abs += d.abs() as f64;
            }
            let n = labels.len() as f64;
            curves
                .entry(format!("{name}_rmse_curve"))
                .or_default()
                .push((sq / n).sqrt());
            curves
                .entry(format!("{name}_mae_curve"))
                .or_default()
                .push(abs / n);
        }
    }
    Ok((booster, curves))
}
#[derive(Clone)]
pub struct TrainingOptions {
    pub params: Map<String, Value>,
    pub holdout_days: usize,
    pub excluded: Vec<String>,
    pub feature_set: Option<String>,
    pub band: bool,
}
impl Default for TrainingOptions {
    fn default() -> Self {
        Self {
            params: Map::new(),
            holdout_days: 60,
            excluded: vec!["partner_investment".into()],
            feature_set: None,
            band: true,
        }
    }
}
impl TrainingOptions {
    fn excluded_set(&self) -> BTreeSet<String> {
        self.excluded.iter().cloned().collect()
    }
}
pub struct TrainedModel {
    pub model: ForecastModel,
    pub curves: BTreeMap<String, Vec<f64>>,
}
fn mean(values: &[f64]) -> f64 {
    kernel::pandas_sum(values) / values.len() as f64
}
fn usable(tx: &[kernel::Transaction], options: &TrainingOptions) -> Result<(String, Table)> {
    let candidates = options
        .feature_set
        .as_ref()
        .map(|n| vec![n.as_str()])
        .unwrap_or_else(|| vec!["components", "level_baseline", "calendar"]);
    for name in candidates {
        let table = training_table(tx, &options.excluded_set(), name, false)?;
        if !levels(name) {
            if table.rows.is_empty() {
                return Err("no usable training days".into());
            }
            return Ok((name.into(), table));
        }
        let flag = table.col("shutdown_flag")?;
        let open_days = table.rows.iter().filter(|r| r[flag] == 0.).count();
        if open_days >= 28 {
            return Ok((name.into(), table));
        }
        if options.feature_set.is_some() {
            return Err(format!(
                "no usable training days: feature set '{name}' needs at least 28 open training days with a recent level (a level needs 7 open days before it); this history has {open_days}"
            ));
        }
    }
    Err("no usable training days".into())
}
fn backtest(
    tx: &[kernel::Transaction],
    table: &Table,
    params: &Map<String, Value>,
    options: &TrainingOptions,
    name: &str,
    curves: &mut BTreeMap<String, Vec<f64>>,
) -> Result<Map<String, Value>> {
    let h = options.holdout_days;
    if h == 0 || table.rows.len() < 2 * h {
        return Ok(Map::new());
    }
    let (train, test) = backtest_split(tx, h, &options.excluded_set(), name)?;
    let cols = kernel::feature_set_columns(name)?;
    let (y_train, y_test) = (train.target(name)?, test.target(name)?);
    let (booster, recorded) = fit(
        params,
        &train,
        &y_train,
        &cols,
        &[
            ("holdout_train", &train, y_train.clone()),
            ("holdout", &test, y_test),
        ],
    )?;
    curves.extend(recorded);
    let mut pred = booster
        .predict(&test.matrix(&cols, None)?)
        .map_err(err)?
        .into_iter()
        .map(|v| v as f64)
        .collect::<Vec<_>>();
    let flag = test.col("shutdown_flag")?;
    for (i, p) in pred.iter_mut().enumerate() {
        if name == "components" {
            *p *= 1. - test.rows[i][flag];
        }
        if offsets(name) {
            *p += test.baseline(&test.rows[i], true)?;
        }
    }
    let net = test.col("net_cash_flow")?;
    let mut truth = test.rows.iter().map(|r| r[net]).collect::<Vec<_>>();
    let history_net = train.col("net_cash_flow")?;
    let mut history = train
        .rows
        .iter()
        .map(|r| r[history_net])
        .collect::<Vec<_>>();
    if name == "components" {
        let seen = tx
            .iter()
            .filter(|r| r.day < test.dates[0])
            .cloned()
            .collect::<Vec<_>>();
        let categories = kernel::FIXED_CATEGORIES
            .iter()
            .filter(|c| !options.excluded.iter().any(|e| e.as_str() == **c))
            .map(|c| c.to_string())
            .collect::<Vec<_>>();
        let schedule = kernel::infer_schedule(&seen, &categories)?;
        let totals = kernel::scheduled_amounts(&schedule, &test.dates, true)?;
        for (i, p) in pred.iter_mut().enumerate() {
            *p += number(&totals["values"][i])?;
        }
        let full = Table::parse(kernel::daily_cash_flow(
            tx,
            None,
            None,
            &options.excluded_set(),
        )?)?;
        let net = full.col("net_cash_flow")?;
        let lookup = full
            .dates
            .iter()
            .copied()
            .zip(full.rows.iter().map(|r| r[net]))
            .collect::<BTreeMap<_, _>>();
        truth = test
            .dates
            .iter()
            .map(|d| lookup.get(d).copied().unwrap_or(0.))
            .collect();
        history = train
            .dates
            .iter()
            .map(|d| lookup.get(d).copied().unwrap_or(0.))
            .collect();
    }
    let errors = pred
        .iter()
        .zip(&truth)
        .map(|(p, t)| p - t)
        .collect::<Vec<_>>();
    let history_mean = mean(&history);
    Ok(json!({"holdout_days":h as f64,"holdout_mae":mean(&errors.iter().map(|v|v.abs()).collect::<Vec<_>>()),"holdout_rmse":mean(&errors.iter().map(|v|v*v).collect::<Vec<_>>()).sqrt(),"holdout_naive_mae":mean(&truth.iter().map(|v|(v-history_mean).abs()).collect::<Vec<_>>()),"holdout_total_error":kernel::pandas_sum(&errors)}).as_object().cloned().unwrap_or_default())
}
pub fn train(tx: &[kernel::Transaction], options: &TrainingOptions) -> Result<TrainedModel> {
    if tx.is_empty() {
        return Err("no transactions to train on".into());
    }
    let (name, table) = usable(tx, options)?;
    let params = defaults(&name, &options.params);
    let columns = kernel::feature_set_columns(&name)?;
    let mut curves = BTreeMap::new();
    let mut metrics = backtest(tx, &table, &params, options, &name, &mut curves)?;
    let target = table.target(&name)?;
    let (booster, recorded) = fit(
        &params,
        &table,
        &target,
        &columns,
        &[("train", &table, target.clone())],
    )?;
    curves.extend(recorded);
    let fitted = booster
        .predict(&table.matrix(&columns, None)?)
        .map_err(err)?;
    let net = table.col("net_cash_flow")?;
    let errors = table
        .rows
        .iter()
        .zip(fitted)
        .map(|(r, p)| {
            Ok(((p as f64
                + if offsets(&name) {
                    table.baseline(r, true)?
                } else {
                    0.
                })
                - r[net])
                .abs())
        })
        .collect::<Result<Vec<_>>>()?;
    metrics.insert("train_mae".into(), json!(mean(&errors)));
    metrics.insert("train_days".into(), json!(table.rows.len() as f64));
    let mut last = *table.dates.last().ok_or("no training dates")?;
    if name == "components" {
        last = last.max(tx.iter().map(|r| r.day).max().ok_or("no transactions")?)
    }
    let mut level_values = BTreeMap::new();
    if levels(&name) {
        let recent = Table::parse(kernel::recent_levels(
            tx,
            &[last + Duration::days(1)],
            &[28, 90],
        )?)?;
        for (c, v) in recent.columns.iter().zip(&recent.rows[0]) {
            level_values.insert(c.clone(), *v);
        }
    }
    let flag = table.col("shutdown_flag")?;
    let baseline_mean = if offsets(&name) {
        mean(
            &table
                .rows
                .iter()
                .filter(|r| r[flag] == 0.)
                .map(|r| table.baseline(r, false))
                .collect::<Result<Vec<_>>>()?,
        )
    } else {
        0.
    };
    let schedule = if name == "components" {
        let cats = kernel::FIXED_CATEGORIES
            .iter()
            .filter(|c| !options.excluded.iter().any(|e| e.as_str() == **c))
            .map(|c| c.to_string())
            .collect::<Vec<_>>();
        Some(kernel::infer_schedule(tx, &cats)?)
    } else {
        None
    };
    let shutdown_days = kernel::shutdown_days(tx, 7, false)?
        .iter()
        .map(|d| (d.month(), d.day()))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let meta = ModelMeta {
        shutdown_days,
        due_days: kernel::infer_due_days(tx, 3),
        trained_until: last,
        params,
        metrics,
        excluded_categories: options.excluded.clone(),
        feature_columns: columns,
        feature_set: name,
        level_values,
        baseline_mean,
        band_spec: None,
        schedule,
    };
    let mut model = ForecastModel::from_bytes(
        &booster.save_buffer(false).map_err(err)?,
        serde_json::to_value(meta).map_err(err)?,
    )?;
    if options.band {
        model.meta.band_spec = band::measure_band(
            tx,
            &model.meta.params,
            &options.excluded_set(),
            &model.meta.feature_set,
        )?;
    }
    Ok(TrainedModel { model, curves })
}
pub fn dispatch(body: &Value) -> Result<Option<Value>> {
    let Some(op) = body["op"].as_str() else {
        return Ok(None);
    };
    if op == "fit_band" {
        let folds = body["folds"]
            .as_array()
            .ok_or("folds")?
            .iter()
            .map(|f| {
                Ok(band::Fold {
                    origin: date(f["origin"].as_str().ok_or("origin")?)?,
                    horizon: f["horizon"].as_i64().ok_or("horizon")?,
                    cumulative_error: number(&f["cum_error"])?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        return Ok(Some(band::fit_band(&folds)?.unwrap_or(Value::Null)));
    }
    if !["train", "table", "split"].contains(&op) {
        return Ok(None);
    }
    let tx = body
        .get("transactions")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(|r| {
            Ok(kernel::Transaction {
                day: date(r["booking_date"].as_str().ok_or("date")?)?,
                amount: number(&r["amount"])?,
                category: r["category"].as_str().map(str::to_string),
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let mut options = TrainingOptions {
        band: body["band"].as_bool().unwrap_or(false),
        ..Default::default()
    };
    options.feature_set = body["feature_set"].as_str().map(str::to_string);
    options.holdout_days = body
        .get("holdout_days")
        .and_then(Value::as_i64)
        .unwrap_or(60)
        .max(0) as usize;
    options.params = body["params"].as_object().cloned().unwrap_or_default();
    if let Some(a) = body["excluded"].as_array() {
        options.excluded = a
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_string)
                    .ok_or_else(|| "category".into())
            })
            .collect::<Result<_>>()?;
    }
    Ok(Some(match op {
        "table" => training_table(
            &tx,
            &options.excluded_set(),
            options.feature_set.as_deref().unwrap_or("calendar"),
            body["keep_closed"].as_bool().unwrap_or(false),
        )?
        .json(),
        "split" => {
            let (a, b) = backtest_split(
                &tx,
                body.get("holdout_days")
                    .and_then(Value::as_u64)
                    .unwrap_or(20) as usize,
                &options.excluded_set(),
                options.feature_set.as_deref().unwrap_or("calendar"),
            )?;
            json!({"train":a.json(),"test":b.json()})
        }
        _ => {
            let trained = train(&tx, &options)?;
            let dates = body["dates"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(|v| date(v.as_str().ok_or("date")?))
                        .collect::<Result<Vec<_>>>()
                })
                .transpose()?
                .unwrap_or_else(|| {
                    date_range(
                        trained.model.meta.trained_until + Duration::days(1),
                        trained.model.meta.trained_until + Duration::days(7),
                    )
                });
            json!({"meta":trained.model.metadata()?,"predict":trained.model.predict(&dates)?,"curves":trained.curves,"importance":trained.model.feature_importances()?})
        }
    }))
}
