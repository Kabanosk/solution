use scout_forecast_training::port::native;
use serde_json::Value;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cases = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/cases.jsonl"))?;
    for (i, line) in cases.lines().enumerate() {
        let case: Value = serde_json::from_str(line)?;
        if i == 214 || i == 217 || i == 220 || i == 235 || i == 256 || i == 286 {
            let body: Value = serde_json::from_str(case["args"][0].as_str().ok_or("input")?)?;
            match native::dispatch(&body) {
                Err(e) => println!("case {i}: {e}"),
                Ok(_) => println!("case {i}: success"),
            }
        }
    }
    Ok(())
}
