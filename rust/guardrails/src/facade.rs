use crate::{client::CheckClient, *};
use async_trait::async_trait;
use chrono::{DateTime, Utc};
use scout_domain::{
    AuditRecord, AuditSink, DmOptions, Guard, GuardOptions, Notifier, People, Verdict,
};
use scout_platform::{Database, Tenant};
use std::{
    collections::{BTreeSet, HashMap},
    sync::{Arc, Mutex, OnceLock},
    time::{Duration, Instant},
};

static SWITCH: OnceLock<Mutex<HashMap<String, (bool, Instant)>>> = OnceLock::new();
pub fn clear_switch_cache(organization: Option<&str>) {
    if let Ok(mut cache) = SWITCH.get_or_init(Default::default).lock() {
        if let Some(id) = organization {
            cache.remove(id);
        } else {
            cache.clear();
        }
    }
}
#[derive(Debug, Clone, Default)]
pub struct GuardSettings {
    pub ceo_id: String,
    pub compliance_contact: String,
    pub dashboard_url: String,
}
pub struct Guardrails {
    pub database: Database,
    pub tenant: Tenant,
    pub client: Arc<dyn CheckClient>,
    pub audit: Arc<dyn AuditSink>,
    pub notifier: Arc<dyn Notifier>,
    pub people: Arc<dyn People>,
    pub settings: GuardSettings,
}
impl Guardrails {
    pub async fn switched_on(&self) -> bool {
        if let Ok(cache) = SWITCH.get_or_init(Default::default).lock()
            && let Some((value, at)) = cache.get(self.tenant.id())
            && at.elapsed() < Duration::from_secs(5)
        {
            return *value;
        };
        let result = async {
            let connection = self.database.scoped(&self.tenant).await?;
            let organization = uuid::Uuid::parse_str(self.tenant.id())?;
            let row = connection
                .query_opt(
                    "SELECT enabled FROM scout_auth.guardrails_settings WHERE organization_id=$1",
                    &[&organization],
                )
                .await?;
            Ok::<_, anyhow::Error>(row.map(|row| row.get::<_, bool>(0)).unwrap_or(true))
        }
        .await;
        let enabled = result.unwrap_or(true);
        if let Ok(mut cache) = SWITCH.get_or_init(Default::default).lock() {
            cache.insert(self.tenant.id().into(), (enabled, Instant::now()));
        }
        enabled
    }
    pub async fn set_switch(
        &self,
        enabled: bool,
        who: &str,
        actor: &str,
        role: Option<&str>,
    ) -> anyhow::Result<bool> {
        let mut connection = self.database.scoped(&self.tenant).await?;
        let transaction = connection.transaction().await?;
        let organization = uuid::Uuid::parse_str(self.tenant.id())?;
        transaction.execute("INSERT INTO scout_auth.guardrails_settings(organization_id) VALUES($1) ON CONFLICT DO NOTHING",&[&organization]).await?;
        let before:bool=transaction.query_one("SELECT enabled FROM scout_auth.guardrails_settings WHERE organization_id=$1 FOR UPDATE",&[&organization]).await?.get(0);
        if before == enabled {
            transaction.commit().await?;
            return Ok(false);
        }
        transaction.execute("UPDATE scout_auth.guardrails_settings SET enabled=$1,updated_by=$2,updated_at=now() WHERE organization_id=$3",&[&enabled,&who,&organization]).await?;
        let state = if enabled { "ON" } else { "OFF" };
        transaction.execute("INSERT INTO audit_log(actor,requester,role,action,target,resource,decision,policy,reason) VALUES($1,$2,$3,'guardrails_toggled','guardrails:workspace',$4,'ALLOW','guardrails-switch',$5)",&[&actor,&who,&role,&format!("NeMo guardrails turned {state}"),&state]).await?;
        transaction.commit().await?;
        clear_switch_cache(Some(self.tenant.id()));
        Ok(true)
    }
    // Keep the source audit fields visible at each call site.
    #[allow(clippy::too_many_arguments)]
    async fn record(
        &self,
        action: &str,
        target: &str,
        resource: &str,
        decision: &str,
        policy: &str,
        reason: &str,
        options: &GuardOptions,
    ) {
        let _ = self
            .audit
            .record(&AuditRecord {
                actor: "agent".into(),
                action: action.into(),
                target: target.into(),
                resource: resource.into(),
                decision: decision.into(),
                task_id: options.task_id.clone(),
                requester: options.requester.clone(),
                role: options.role.clone(),
                policy: Some(policy.into()),
                reason: Some(reason.into()),
            })
            .await;
    }
    async fn audit_block(
        &self,
        config: &str,
        texts: &[String],
        value: &Verdict,
        options: &GuardOptions,
    ) {
        let mut options = options.clone();
        options.role = None;
        let source = if options.source.is_empty() {
            config
        } else {
            &options.source
        };
        self.record(
            "guardrail_check",
            &format!("guardrails:{config}"),
            &format!("{source}: {}", digest(&texts.join("\n"))),
            "BLOCK",
            config,
            &value.rails(),
            &options,
        )
        .await;
    }
    pub async fn check(
        &self,
        config: &str,
        texts: &[String],
        options: &GuardOptions,
        record_block: bool,
    ) -> Verdict {
        let texts: Vec<_> = texts
            .iter()
            .filter(|text| !text.trim().is_empty())
            .cloned()
            .collect();
        if !self.client.enabled() || texts.is_empty() || !self.switched_on().await {
            return verdict("disabled");
        };
        let result = if texts.iter().map(|text| chunks(text).len()).sum::<usize>() > MAX_CHUNKS {
            Verdict {
                activated_rails: vec!["too long to check".into()],
                ..verdict("blocked")
            }
        } else {
            self.client.check(config, &messages(config, &texts)).await
        };
        if result.failed() {
            let mut options = options.clone();
            options.role = None;
            let source = if options.source.is_empty() {
                config
            } else {
                &options.source
            };
            self.record(
                "guardrail_unavailable",
                &format!("guardrails:{config}"),
                source,
                "ALLOW",
                config,
                if result.detail.is_empty() {
                    &result.status
                } else {
                    &result.detail
                },
                &options,
            )
            .await;
        } else if result.blocked() && record_block {
            self.audit_block(config, &texts, &result, options).await;
        }
        result
    }
    async fn compliance_target(&self) -> String {
        let target = self.settings.compliance_contact.trim();
        if target.is_empty() {
            return self.settings.ceo_id.clone();
        };
        if self.people.employee(target).await.ok().flatten().is_some() {
            return target.into();
        };
        self.people
            .by_slack_id(target)
            .await
            .ok()
            .flatten()
            .and_then(|row| row["id"].as_str().map(str::to_owned))
            .unwrap_or_else(|| target.into())
    }
    async fn strike(
        &self,
        person: Option<&str>,
        prompt: &str,
        rails: &str,
        options: &GuardOptions,
    ) -> anyhow::Result<usize> {
        let who = person
            .filter(|value| !value.is_empty())
            .unwrap_or("unknown");
        let mut connection = self.database.scoped(&self.tenant).await?;
        let transaction = connection.transaction().await?;
        transaction
            .execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                &[&format!("guardrail-strikes:{who}")],
            )
            .await?;
        transaction
            .execute(
                "INSERT INTO guardrail_strikes(person,prompt,rails) VALUES($1,$2,$3)",
                &[&who, &prompt, &rails],
            )
            .await?;
        let mut human = options.clone();
        human.requester = person.map(str::to_owned);
        audit_sql(
            &transaction,
            "guardrail_check",
            &format!("guardrails:{USER_INPUT}"),
            &format!("{USER_INPUT}: {}", digest(prompt)),
            "BLOCK",
            USER_INPUT,
            rails,
            &human,
        )
        .await?;
        let rows=transaction.query("SELECT id,ts,prompt,rails FROM guardrail_strikes WHERE person=$1 AND reported_at IS NULL AND ts>now()-7*interval '1 day' ORDER BY id",&[&who]).await?;
        let count = rows.len();
        if count >= 3 {
            let target = self.compliance_target().await;
            let mut rails = BTreeSet::new();
            let mut listing = Vec::new();
            let mut short = Vec::new();
            let mut ids = Vec::new();
            for (index, row) in rows.iter().enumerate() {
                ids.push(row.get::<_, i64>("id"));
                let date: DateTime<Utc> = row.get("ts");
                let prompt: String = row.get("prompt");
                let value: Option<String> = row.get("rails");
                rails.extend(
                    value
                        .unwrap_or_default()
                        .split(", ")
                        .filter(|part| !part.is_empty())
                        .map(str::to_owned),
                );
                listing.push(format!(
                    "{}. {} UTC\n{prompt}",
                    index + 1,
                    date.format("%Y-%m-%d %H:%M")
                ));
                short.push(format!(
                    "{}. {} UTC: “{}”{}",
                    index + 1,
                    date.format("%Y-%m-%d %H:%M"),
                    chars(&prompt, 700),
                    if prompt.chars().count() > 700 {
                        " (shortened)"
                    } else {
                        ""
                    }
                ));
            }
            let rails = rails.into_iter().collect::<Vec<_>>().join(", ");
            let name = if person.is_some() {
                self.people
                    .name(person)
                    .await
                    .unwrap_or_else(|_| person.unwrap_or("").into())
            } else {
                "someone".into()
            };
            let role = options
                .role
                .as_deref()
                .filter(|role| !role.is_empty())
                .unwrap_or("unknown role")
                .replace('_', " ");
            let head = format!(
                "Compliance alert: {name} ({role}) had {count} messages blocked by the safety policy within 7 days. Rails: {}.",
                if rails.is_empty() { "unknown" } else { &rails }
            );
            let uploaded = self
                .notifier
                .upload(
                    &target,
                    &[(
                        format!("blocked-prompts-{who}.txt"),
                        listing.join("\n\n").into_bytes(),
                    )],
                    &format!("The {count} blocked prompts of {who}, in full."),
                )
                .await
                .ok()
                .flatten()
                .filter(|value| !value.is_empty())
                .is_some();
            let text = if uploaded {
                format!("{head}\nThe full prompts are in the file sent with this message.")
            } else {
                format!("{head}\nThe blocked prompts:\n{}", short.join("\n"))
            };
            let reference = self
                .notifier
                .dm(
                    &target,
                    &text,
                    "NOTIFY",
                    &DmOptions {
                        task_id: options.task_id.clone(),
                        ..Default::default()
                    },
                )
                .await
                .ok()
                .flatten();
            if reference.is_some() {
                transaction
                    .execute(
                        "UPDATE guardrail_strikes SET reported_at=now() WHERE id=ANY($1)",
                        &[&ids],
                    )
                    .await?;
                audit_sql(
                    &transaction,
                    "guardrail_escalation",
                    &format!("slack:{target}"),
                    &format!("{count} blocked prompts of {who} reported to compliance"),
                    "ALLOW",
                    USER_INPUT,
                    &rails,
                    &human,
                )
                .await?;
            } else {
                audit_sql(
                    &transaction,
                    "guardrail_escalation_failed",
                    &format!("slack:{target}"),
                    &format!("{count} blocked prompts of {who} not delivered to compliance"),
                    "DENY",
                    USER_INPUT,
                    &rails,
                    &human,
                )
                .await?;
            }
        }
        transaction.commit().await?;
        Ok(count)
    }
    async fn notify_ceo_once(
        &self,
        key: &str,
        label: &str,
        rails: &str,
        task_id: Option<&str>,
    ) -> anyhow::Result<()> {
        let mut connection = self.database.scoped(&self.tenant).await?;
        let transaction = connection.transaction().await?;
        transaction
            .execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                &[&format!("guardrail-alert:{key}")],
            )
            .await?;
        if transaction
            .query_opt("SELECT 1 FROM guardrail_alerts WHERE item_key=$1", &[&key])
            .await?
            .is_some()
        {
            return Ok(());
        };
        let ceo = &self.settings.ceo_id;
        let text = format!(
            "Suspicious content from an outside source ({label}) was blocked before Scout read it. Rails: {rails}."
        );
        let reference = self
            .notifier
            .dm(
                ceo,
                &text,
                "NOTIFY",
                &DmOptions {
                    task_id: task_id.map(str::to_owned),
                    ..Default::default()
                },
            )
            .await?;
        if reference.is_some() {
            transaction.execute("INSERT INTO guardrail_alerts(item_key,rails) VALUES($1,$2) ON CONFLICT DO NOTHING",&[&key,&rails]).await?;
            audit_sql(
                &transaction,
                "guardrail_escalation",
                &format!("slack:{ceo}"),
                &format!("owner told about {label}"),
                "ALLOW",
                "suspicious-content",
                rails,
                &GuardOptions {
                    task_id: task_id.map(str::to_owned),
                    ..Default::default()
                },
            )
            .await?;
        }
        transaction.commit().await?;
        Ok(())
    }
}
#[allow(clippy::too_many_arguments)]
async fn audit_sql(
    transaction: &tokio_postgres::Transaction<'_>,
    action: &str,
    target: &str,
    resource: &str,
    decision: &str,
    policy: &str,
    reason: &str,
    options: &GuardOptions,
) -> anyhow::Result<()> {
    let resource = chars(resource, 500);
    transaction.execute("INSERT INTO audit_log(task_id,actor,requester,role,action,target,resource,decision,policy,reason) VALUES($1,'agent',$2,$3,$4,$5,$6,$7,$8,$9)",&[&options.task_id,&options.requester,&options.role,&action,&target,&resource,&decision,&policy,&reason]).await?;
    Ok(())
}
#[async_trait]
impl Guard for Guardrails {
    async fn active(&self) -> bool {
        self.client.enabled() && self.switched_on().await
    }
    async fn screen_human(
        &self,
        person: Option<&str>,
        text: &str,
        options: &GuardOptions,
    ) -> Option<String> {
        let mut options = options.clone();
        options.requester = person.map(str::to_owned);
        options.source.clear();
        let result = self
            .check(USER_INPUT, &[text.into()], &options, false)
            .await;
        if !result.blocked() {
            return None;
        };
        let count = self
            .strike(person, text, &result.rails(), &options)
            .await
            .unwrap_or(1);
        Some(blocked_reply(count.min(3)))
    }
    async fn screen_tool(
        &self,
        tool: &str,
        content: &str,
        result: Option<&Value>,
        options: &GuardOptions,
    ) -> String {
        let mut options = options.clone();
        options.source = format!("tool {tool}");
        let checked = self
            .check(TOOL_RESULT, &[content.into()], &options, true)
            .await;
        if !checked.blocked() {
            return content.into();
        };
        if ["search_email", "search_chat", "read_file", "search_files"].contains(&tool) {
            let items = outside_items(tool, result.unwrap_or(&Value::Null));
            let mut flagged = Vec::new();
            if items.len() == 1 {
                flagged.push((items[0].0.clone(), items[0].1.clone(), checked.rails()));
            } else {
                for (key, label, text) in items {
                    let mut item = options.clone();
                    item.source = format!("{tool} item");
                    item.requester = None;
                    let value = self.check(TOOL_RESULT, &[text], &item, false).await;
                    if value.blocked() {
                        flagged.push((key, label, value.rails()));
                    }
                }
            }
            if flagged.is_empty() {
                flagged.push((
                    format!("tool:{tool}:{}", sha(&[content.into()])),
                    format!("tool {tool}"),
                    checked.rails(),
                ));
            }
            for (key, label, rails) in flagged {
                self.alert_outside(&key, &label, &rails, options.task_id.as_deref())
                    .await;
            }
        }
        WITHHELD.into()
    }
    async fn screen_document(&self, text: &str, options: &GuardOptions) -> Verdict {
        let mut options = options.clone();
        options.requester = None;
        self.check(TOOL_RESULT, &[text.into()], &options, true)
            .await
    }
    async fn screen_output(&self, text: &str, options: &GuardOptions) -> String {
        let mut supplied = options.clone();
        if supplied.source.is_empty() {
            supplied.source = "reply".into();
        }
        let options = &supplied;
        if !self.active().await {
            return text.into();
        };
        let dashboard = self.settings.dashboard_url.trim_end_matches('/');
        let swap = dashboard.starts_with("http") && text.contains(dashboard);
        let mut work = if swap {
            text.replace(dashboard, DASHBOARD_PLACEHOLDER)
        } else {
            text.into()
        };
        let config = if matches!(options.role.as_deref(), Some("ceo" | "office_manager")) {
            BOT_OUTPUT_FINANCE
        } else {
            let (masked, kinds) = mask_sensitive(&work);
            work = masked;
            if !kinds.is_empty() {
                self.record(
                    "guardrail_mask",
                    &format!("guardrails:{BOT_OUTPUT}"),
                    &format!("masked: {} in {}", kinds.join(", "), options.source),
                    "MASK",
                    "bot-output-mask",
                    &kinds.join(", "),
                    options,
                )
                .await;
            }
            BOT_OUTPUT
        };
        let result = self.check(config, &[work.clone()], options, true).await;
        if result.blocked() {
            OUTPUT_REPLY.into()
        } else if swap {
            work.replace(DASHBOARD_PLACEHOLDER, dashboard)
        } else {
            work
        }
    }
    async fn screen_history(&self, turns: &[Value], options: &GuardOptions) -> Vec<Value> {
        let mut out = turns.to_vec();
        for (role, config) in [("assistant", TOOL_RESULT), ("user", USER_INPUT)] {
            let indexes: Vec<_> = turns
                .iter()
                .enumerate()
                .filter_map(|(i, turn)| {
                    (turn["role"] == role
                        && !turn["content"].as_str().unwrap_or("").trim().is_empty())
                    .then_some(i)
                })
                .collect();
            for batch in batches(turns, &indexes) {
                if batch.is_empty() {
                    continue;
                };
                let mut options = options.clone();
                options.source = format!("earlier {role} turns");
                let texts = batch
                    .iter()
                    .map(|i| turns[*i]["content"].as_str().unwrap_or("").into())
                    .collect::<Vec<_>>();
                let result = self.check(config, &texts, &options, true).await;
                if !result.blocked() {
                    continue;
                };
                if batch.len() > MAX_RECHECKS {
                    for i in batch {
                        out[i]["content"] = json!(WITHHELD)
                    }
                    continue;
                };
                for i in batch {
                    options.source = format!("earlier {role} turn");
                    let result = self
                        .check(
                            config,
                            &[turns[i]["content"].as_str().unwrap_or("").into()],
                            &options,
                            false,
                        )
                        .await;
                    if result.blocked() || result.failed() {
                        out[i]["content"] = json!(WITHHELD)
                    }
                }
            }
        }
        out
    }
    async fn alert_outside(&self, key: &str, label: &str, rails: &str, task_id: Option<&str>) {
        let _ = self.notify_ceo_once(key, label, rails, task_id).await;
    }
}
