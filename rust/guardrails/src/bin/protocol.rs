use scout_guardrails::{HttpGuardrails,client::CheckClient,USER_INPUT};
use serde_json::{Value,json};
use std::{io::{self,Read},time::Duration};
#[tokio::main(flavor="current_thread")]
async fn main(){let mut text=String::new();io::stdin().read_to_string(&mut text).unwrap();let data:Value=serde_json::from_str(&text).unwrap();let client=HttpGuardrails::new(data["url"].as_str().unwrap_or(""),data["token"].as_str().unwrap_or("test-token"),"scout-test",None).unwrap();let mut values=Vec::new();for call in data["calls"].as_array().unwrap(){let value=if call["op"]=="warm"{client.warm_up(Duration::from_secs(30)).await}else{client.check(call["config"].as_str().unwrap_or(USER_INPUT),call["messages"].as_array().map(Vec::as_slice).unwrap_or(&[])).await};values.push(json!({"status":value.status,"activated_rails":value.activated_rails,"detail":value.detail}));}println!("{}",json!(values));}
