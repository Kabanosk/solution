//! HTTP adapter for the native Scout repository; every unported route remains explicit fallback.
use crate::original::{self, Original};
use axum::{
    Json,
    extract::{Request, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use scout_api::{AuditOptions, ConnectionSettings, ScoutApi, TaskListOptions};
use scout_platform::{Database, Tenant};
use scout_finance_api::{FinanceApi, FinanceOptions};
use serde_json::json;
use std::collections::HashMap;

fn query(request: &Request) -> HashMap<String, String> {
    form_urlencoded::parse(request.uri().query().unwrap_or("").as_bytes())
        .into_owned()
        .collect()
}

struct QueryError {
    message: String,
    kind: &'static str,
}

impl IntoResponse for QueryError {
    fn into_response(self) -> Response {
        let message = self.message;
        (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"error":{
            "code":"validation_error", "message":format!("Invalid request: query.limit: {message}."),
            "details":[{"loc":["query","limit"],"message":message,"type":self.kind}]
        }}))).into_response()
    }
}

fn invalid_limit(message: String, kind: &'static str) -> QueryError {
    QueryError { message, kind }
}

/// Pydantic's integer query syntax includes integer underscores and zero-only decimal tails.
/// Compare large valid integers to the bounds without overflowing a machine integer.
fn limit(
    parameters: &HashMap<String, String>,
    default: i64,
    maximum: i64,
) -> Result<i64, QueryError> {
    let Some(input) = parameters.get("limit") else {
        return Ok(default);
    };
    let text = input.trim();
    let (negative, digits) = if let Some(rest) = text.strip_prefix('-') {
        (true, rest)
    } else {
        (false, text.strip_prefix('+').unwrap_or(text))
    };
    let components: Vec<_> = digits.split('.').collect();
    let valid_digits =
        |part: &str| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit());
    let decimal = components.len() == 2
        && valid_digits(components[0])
        && !components[1].is_empty()
        && components[1].bytes().all(|byte| byte == b'0');
    let integer = components.len() == 1
        && !digits.is_empty()
        && digits.as_bytes().first().is_some_and(u8::is_ascii_digit)
        && digits.as_bytes().last().is_some_and(u8::is_ascii_digit)
        && digits
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte == b'_')
        && !digits.contains("__");
    if !(integer || decimal) {
        return Err(invalid_limit(
            "Input should be a valid integer, unable to parse string as an integer".into(),
            "int_parsing",
        ));
    }
    if text.len() > 4300 {
        return Err(invalid_limit(
            "Unable to parse input string as an integer, exceeded maximum size".into(),
            "int_parsing_size",
        ));
    }
    let number = components[0].replace('_', "");
    let normalized = number.trim_start_matches('0');
    let value = normalized
        .parse::<i64>()
        .unwrap_or(if normalized.is_empty() { 0 } else { i64::MAX });
    if (negative && value != 0) || value < 1 {
        return Err(invalid_limit(
            "Input should be greater than or equal to 1".into(),
            "greater_than_equal",
        ));
    }
    if value > maximum {
        return Err(invalid_limit(
            format!("Input should be less than or equal to {maximum}"),
            "less_than_equal",
        ));
    }
    Ok(value)
}

fn cors(request: &Request, mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::VARY, header::HeaderValue::from_static("Origin"));
    if let Some(origin) = request.headers().get(header::ORIGIN) {
        response.headers_mut().insert(
            header::ACCESS_CONTROL_EXPOSE_HEADERS,
            header::HeaderValue::from_static("X-Stub"),
        );
        let configured = std::env::var("CORS_ORIGINS")
            .ok()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "*".into());
        let origins: Vec<_> = configured
            .split(',')
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .collect();
        if origins.contains(&"*") {
            response.headers_mut().insert(
                header::ACCESS_CONTROL_ALLOW_ORIGIN,
                header::HeaderValue::from_static("*"),
            );
        } else if origins
            .iter()
            .any(|allowed| allowed.as_bytes() == origin.as_bytes())
        {
            response
                .headers_mut()
                .insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin.clone());
        }
    }
    response
}

fn api_response(result: scout_api::Result<serde_json::Value>) -> Response {
    match result {
        Ok(value) => Json(value).into_response(),
        Err(error) => (
            StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            Json(error.json()),
        )
            .into_response(),
    }
}

pub async fn dispatch(State(original): State<Original>, request: Request) -> Response {
    let path = percent_encoding::percent_decode_str(request.uri().path()).decode_utf8_lossy();
    let method = request.method().as_str();
    let task_id = path
        .strip_prefix("/v1/tasks/")
        .filter(|id| !id.contains('/') && !id.is_empty() && *id != "stream");
    let invoice_id = path
        .strip_prefix("/v1/transactions/")
        .and_then(|suffix| suffix.strip_suffix("/invoice"))
        .filter(|id| !id.contains('/') && !id.is_empty());
    let approval = path
        .strip_prefix("/v1/approvals/")
        .and_then(|suffix| {
            suffix
                .strip_suffix(":approve")
                .map(|id| (id, true))
                .or_else(|| suffix.strip_suffix(":reject").map(|id| (id, false)))
        })
        .filter(|(id, _)| !id.is_empty() && !id.contains('/'));
    let known_read = method == "GET"
        && (matches!(
            path.as_ref(),
            "/v1/tasks" | "/v1/approvals" | "/v1/audit" | "/v1/connections" | "/v1/kpis" | "/v1/forecast/liquidity" | "/v1/costs/by-team" | "/v1/cost-metrics"
        ) || task_id.is_some()
            || invoice_id.is_some());
    let known_write = method == "POST" && (approval.is_some() || path == "/v1/strategy/review");
    if !(known_read || known_write) {
        return original::proxy(State(original), request).await;
    }
    let Some(tenant) = request.extensions().get::<Tenant>().cloned() else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error":{"code":"internal_error","message":"Internal server error."}})),
        )
            .into_response();
    };
    let database_url = std::env::var("SCOUT_RUST_TEST_DATABASE_URL")
        .or_else(|_| std::env::var("DATABASE_URL"))
        .unwrap_or_else(|_| "postgresql://cfo:cfo@localhost:5433/cfo".into());
    let database=Database::new(database_url);
    let api = ScoutApi::new(database.clone(), tenant.clone());
    let parameters = query(&request);
    if matches!(path.as_ref(),"/v1/kpis" | "/v1/forecast/liquidity" | "/v1/costs/by-team" | "/v1/cost-metrics") {
        let mut validation=crate::validation::Validation::default();
        let mut horizon=90;
        let mut granularity="week";
        let mut from=None;
        let mut to=None;
        let mut kind=None;
        match path.as_ref() {
            "/v1/forecast/liquidity"=>{horizon=validation.integer(&parameters,"horizon_days",90,7,365);granularity=validation.choice(&parameters,"granularity",&["day","week"],true).unwrap_or("week");},
            "/v1/costs/by-team"=>{from=validation.date(&parameters,"from");to=validation.date(&parameters,"to");},
            "/v1/cost-metrics"=>{kind=validation.choice(&parameters,"kind",&["food_cost","ads_cac","cloud_per_order"],false);},
            _=>{}
        }
        if let Some(error)=validation.envelope(){return cors(&request,(StatusCode::UNPROCESSABLE_ENTITY,Json(error)).into_response());}
        let mut options=FinanceOptions::default();
        if let Ok(path)=std::env::var("FORECAST_MODEL_PATH") && !path.is_empty(){options.model_path=path.into();}
        options.safety_buffer_eur=std::env::var("SAFETY_BUFFER_EUR").ok().filter(|value|!value.is_empty());
        let finance=FinanceApi::new(database,tenant,options);
        let result=match path.as_ref(){
            "/v1/kpis"=>finance.kpis().await,
            "/v1/forecast/liquidity"=>finance.forecast(horizon as usize,granularity).await,
            "/v1/costs/by-team"=>finance.costs_by_team(from,to).await,
            "/v1/cost-metrics"=>finance.cost_metrics(kind).await,
            _=>unreachable!(),
        };
        return cors(&request,api_response(result));
    }
    let result = if known_write {
        if let Some((id, approved)) = approval {
            api.answer_approval(id, approved).await
        } else {
            api.strategy_review().await
        }
    } else if let Some(id) = task_id {
        api.get_task(id).await
    } else if let Some(id) = invoice_id {
        api.transaction_invoice(id).await
    } else {
        match path.as_ref() {
            "/v1/tasks" => {
                let maximum = match limit(&parameters, 50, 200) {
                    Ok(value) => value,
                    Err(error) => return cors(&request, error.into_response()),
                };
                api.list_tasks(&TaskListOptions {
                    status: parameters.get("status").cloned(),
                    r#loop: parameters.get("loop").cloned(),
                    owner: parameters.get("owner").cloned(),
                    q: parameters.get("q").cloned(),
                    limit: maximum,
                })
                .await
            }
            "/v1/approvals" => {
                api.list_approvals(parameters.get("status").map_or("pending", String::as_str))
                    .await
            }
            "/v1/audit" => {
                let maximum = match limit(&parameters, 100, 500) {
                    Ok(value) => value,
                    Err(error) => return cors(&request, error.into_response()),
                };
                api.audit(&AuditOptions {
                    task_id: parameters.get("task_id").cloned(),
                    decision: parameters.get("decision").cloned(),
                    limit: maximum,
                })
                .await
            }
            "/v1/connections" => api.connections(&ConnectionSettings::from_env()).await,
            _ => Err(scout_api::ApiError::new(
                500,
                "internal_error",
                "Internal server error.",
            )),
        }
    };
    cors(&request, api_response(result))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn limits_accept_source_integer_syntax_and_reject_fractional_values() {
        for (input, wanted) in [("1.00", 1), ("+10", 10), (" 10 ", 10), ("1_0", 10)] {
            assert!(
                matches!(limit(&HashMap::from([("limit".into(),input.into())]),50,200),Ok(value) if value==wanted)
            );
        }
        for input in [
            ".0",
            "1.",
            "1__0",
            "1.0_0",
            "1.2",
            "١٢",
            "0",
            "99999999999999999999999",
        ] {
            assert!(limit(&HashMap::from([("limit".into(), input.into())]), 50, 200).is_err());
        }
    }
}
