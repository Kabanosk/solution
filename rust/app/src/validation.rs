//! Pydantic-compatible typed query values and the source validation envelope.
use chrono::NaiveDate;
use serde_json::{Value,json};
use std::collections::HashMap;
use strum::EnumMessage;
#[derive(Default)]
pub struct Validation {pub details:Vec<Value>}
impl Validation {
    fn error(&mut self,name:&str,message:impl Into<String>,kind:&str){self.details.push(json!({"loc":["query",name],"message":message.into(),"type":kind}));}
    pub fn envelope(&self)->Option<Value>{self.details.first().map(|first|json!({"error":{"code":"validation_error","message":format!("Invalid request: query.{}: {}.",first["loc"][1].as_str().unwrap_or(""),first["message"].as_str().unwrap_or("")),"details":self.details}}))}
    pub fn integer(&mut self,values:&HashMap<String,String>,name:&str,default:i64,minimum:i64,maximum:i64)->i64 {
        let Some(raw)=values.get(name)else{return default};let text=raw.trim();let (negative,digits)=if let Some(rest)=text.strip_prefix('-'){(true,rest)}else{(false,text.strip_prefix('+').unwrap_or(text))};
        let parts:Vec<_>=digits.split('.').collect();let plain=|part:&str|!part.is_empty()&&part.bytes().all(|byte|byte.is_ascii_digit());
        let decimal=parts.len()==2&&plain(parts[0])&&!parts[1].is_empty()&&parts[1].bytes().all(|byte|byte==b'0');
        let integer=parts.len()==1&&!digits.is_empty()&&digits.as_bytes().first().is_some_and(u8::is_ascii_digit)&&digits.as_bytes().last().is_some_and(u8::is_ascii_digit)&&digits.bytes().all(|byte|byte.is_ascii_digit()||byte==b'_')&&!digits.contains("__");
        if !(integer||decimal){self.error(name,"Input should be a valid integer, unable to parse string as an integer","int_parsing");return default};
        if text.len()>4300{self.error(name,"Unable to parse input string as an integer, exceeded maximum size","int_parsing_size");return default};
        let number=parts[0].replace('_',"");let normalized=number.trim_start_matches('0');let absolute=normalized.parse::<i64>().unwrap_or(if normalized.is_empty(){0}else{i64::MAX});let value=if negative{-absolute}else{absolute};
        if value<minimum{self.error(name,format!("Input should be greater than or equal to {minimum}"),"greater_than_equal")}else if value>maximum{self.error(name,format!("Input should be less than or equal to {maximum}"),"less_than_equal")};value
    }
    pub fn choice<'a>(&mut self,values:&'a HashMap<String,String>,name:&str,options:&[&str],literal:bool)->Option<&'a str>{let value=values.get(name)?.as_str();if !options.contains(&value){let quoted=options.iter().map(|value|format!("'{value}'")).collect::<Vec<_>>();let expected=if quoted.len()==2{quoted.join(" or ")}else{format!("{} or {}",quoted[..quoted.len()-1].join(", "),quoted.last().unwrap())};self.error(name,format!("Input should be {expected}"),if literal{"literal_error"}else{"enum"});}Some(value)}
    pub fn date(&mut self,values:&HashMap<String,String>,name:&str)->Option<NaiveDate>{let value=values.get(name)?;let parsed=match speedate::Date::parse_str_rfc3339(value){Ok(date)=>Ok(date),Err(_)=>match speedate::DateTime::parse_str(value){Ok(datetime) if datetime.time.hour==0&&datetime.time.minute==0&&datetime.time.second==0&&datetime.time.microsecond==0=>Ok(datetime.date),Ok(_)=>{self.error(name,"Datetimes provided to dates should have zero time - e.g. be exact dates","date_from_datetime_inexact");return None},Err(error)=>Err(error)}};match parsed{Ok(date) if date.year!=0=>NaiveDate::from_ymd_opt(date.year as i32,date.month as u32,date.day as u32),Ok(_)=>{self.error(name,"Input should be a valid date in the format YYYY-MM-DD, year 0 is out of range","date_parsing");None},Err(error)=>{self.error(name,format!("Input should be a valid date or datetime, {}",error.get_documentation().unwrap_or("invalid date")),"date_from_datetime_parsing");None}}}
}
