use scout_dashboard::overview_model::*;
use serde_json::{Value, json};
use std::io::{self, BufRead};
fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.expect("fixture line");
        let v: Value = serde_json::from_str(&line).expect("fixture JSON");
        let args = v["args"].as_array().expect("args");
        let number = |i: usize| args[i].as_f64().unwrap_or(0.0);
        let result = match v["op"].as_str().unwrap_or("") {
            "latestWhatIf" => json!(latest_what_if(args[0].as_array().unwrap())),
            "withWhatIf" => json!(with_what_if(
                args[0].as_array().unwrap(),
                args.get(1).filter(|v| !v.is_null())
            )),
            "niceTicks" => json!(nice_ticks(number(0), number(1))),
            "bufferStatus" => json!(buffer_status(number(0))),
            "gaugeStatus" => json!(gauge_status(number(0), number(1), number(2))),
            "formatCoverage" => json!(coverage_label(number(0))),
            "scoutOutcome" => json!(scout_outcome(&args[0])),
            "scoutRing" => scout_ring(args[0].as_array().unwrap(), args[1].as_str().unwrap()),
            "notableActions" => json!(notable_actions(
                args[0].as_array().unwrap(),
                args.get(1).and_then(Value::as_u64).unwrap_or(3) as usize
            )),
            "actionSummary" => json!(action_summary(&args[0])),
            _ => panic!("unknown fixture operation"),
        };
        println!("{result}");
    }
}
