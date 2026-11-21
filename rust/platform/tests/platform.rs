use scout_platform::{
    AuthError, Database, PlatformError, Tenant, authenticate_request, authenticate_websocket,
    legacy_connectors_enabled, load_worker_tenant, require_write_identity, scoped_schema,
    valid_session_input, verify_session,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

type TestResult = Result<(), Box<dyn std::error::Error>>;
fn platform_error(error: PlatformError) -> Value {
    let name = match error {
        PlatformError::Permission(_) => "PermissionError",
        PlatformError::Value(_) => "ValueError",
        _ => "OtherError",
    };
    json!({"error_type":name,"message":error.to_string()})
}
fn auth_error(error: AuthError) -> Value {
    json!({"status":error.status,"error":{"code":error.code,"message":error.message}})
}
#[test]
fn replay_original_pure_platform_corpus() -> TestResult {
    for line in include_str!("../characterization.jsonl").lines() {
        let case: Value = serde_json::from_str(line)?;
        let body = &case["input"];
        let tenant = if body["tenant"].is_null() {
            Ok(None)
        } else {
            let t = &body["tenant"];
            Tenant::new(
                t["id"].as_str().ok_or("id")?,
                t["slug"].as_str().ok_or("slug")?,
                t["schema_name"].as_str().ok_or("schema")?,
                t["employee_id"].as_str().map(str::to_string),
                t["role"].as_str().ok_or("role")?,
                t["legacy"].as_bool().unwrap_or(false),
            )
            .map(Some)
        };
        let output = match tenant {
            Err(error) => platform_error(error),
            Ok(tenant) => match body["op"].as_str().ok_or("op")? {
                "tenant" => json!({"ok":tenant}),
                "schema" => match scoped_schema(tenant.as_ref(), body["schema"].as_str()) {
                    Ok(schema) => json!({"ok":schema}),
                    Err(error) => platform_error(error),
                },
                "legacy" => {
                    json!({"ok":legacy_connectors_enabled(tenant.as_ref(),body["configured"].as_str().unwrap_or_default())})
                }
                "write" => match require_write_identity(tenant.as_ref()) {
                    Ok(id) => json!({"ok":id}),
                    Err(error) => auth_error(error),
                },
                "session_input" => {
                    json!({"ok":valid_session_input(body["token"].as_str().ok_or("token")?,body["slug"].as_str().ok_or("slug")?)})
                }
                op => return Err(format!("unknown {op}").into()),
            },
        };
        assert_eq!(output, case["output"], "input {body}");
    }
    Ok(())
}
#[test]
fn deserialize_cannot_bypass_registration() -> TestResult {
    let invalid = json!({"id":"11111111-2222-4333-8444-555555555555","slug":"normal","schema_name":"public","employee_id":"owner","role":"owner","legacy":false});
    assert!(serde_json::from_value::<Tenant>(invalid).is_err());
    assert!(scoped_schema(None, None).is_err());
    Ok(())
}
#[tokio::test]
async fn missing_malformed_duplicate_headers_never_need_database() -> TestResult {
    let db = Database::new("postgresql://unused:unused@127.0.0.1:1/unused?connect_timeout=1");
    for method in ["GET", "HEAD"] {
        assert!(
            authenticate_request(&db, method, "/healthz", &[])
                .await?
                .is_none()
        );
    }
    for headers in [
        vec![],
        vec![("authorization".into(), "Basic x".into())],
        vec![
            ("authorization".into(), format!("Bearer {}", "a".repeat(40))),
            ("Authorization".into(), "duplicate".into()),
            ("X-Scout-Organization".into(), "normal".into()),
        ],
        vec![
            ("authorization".into(), "Bearer bad".into()),
            ("X-Scout-Organization".into(), "normal".into()),
        ],
    ] {
        let error = authenticate_request(&db, "GET", "/v1/tasks", &headers)
            .await
            .err()
            .ok_or("missing error")?;
        assert_eq!(error.status, 401);
        assert_eq!(error.www_authenticate(), Some("Bearer"));
    }
    assert_eq!(
        authenticate_request(&db, "POST", "/healthz", &[])
            .await
            .err()
            .ok_or("POST bypass")?
            .status,
        401
    );
    assert_eq!(
        authenticate_websocket(&db, "/healthz", &[])
            .await
            .err()
            .ok_or("websocket bypass")?
            .websocket_close_code(),
        4401
    );
    let headers = vec![
        ("authorization".into(), format!("Bearer {}", "a".repeat(40))),
        ("x-scout-organization".into(), "normal".into()),
    ];
    assert_eq!(
        authenticate_request(&db, "GET", "/v1/tasks", &headers)
            .await
            .err()
            .ok_or("missing database error")?
            .status,
        503
    );
    assert!(verify_session(&db, "bad", "normal").await?.is_none());
    Ok(())
}

#[tokio::test]
#[ignore = "requires explicit SCOUT_TEST_DATABASE_URL pointing to the isolated migration database"]
async fn postgres_sessions_workers_and_parallel_tenant_isolation() -> TestResult {
    let url = std::env::var("SCOUT_TEST_DATABASE_URL")?;
    let db = Database::new(url);
    let admin = db.unscoped().await?;
    admin
        .batch_execute(include_str!("../../../backend/cfo/db/auth.sql"))
        .await?;
    let mut fixtures = Vec::new();
    for index in 0..2 {
        let organization = Uuid::new_v4();
        let user = Uuid::new_v4();
        let schema = format!("tenant_{}", organization.simple());
        let slug = format!("rust-platform-{}", organization.simple());
        let employee = format!("owner-{index}");
        let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
        let digest = format!("{:x}", Sha256::digest(token.as_bytes()));
        admin.execute("INSERT INTO scout_auth.organizations (id,slug,name,schema_name) VALUES ($1,$2,'Rust platform isolated fixture',$3)",&[&organization,&slug,&schema]).await?;
        admin
            .execute(
                "INSERT INTO scout_auth.users (id,email,name) VALUES ($1,$2,'Fixture')",
                &[&user, &format!("{user}@example.invalid")],
            )
            .await?;
        admin.execute("INSERT INTO scout_auth.memberships (organization_id,user_id,role,employee_id) VALUES ($1,$2,'owner',$3)",&[&organization,&user,&employee]).await?;
        admin.execute("INSERT INTO scout_auth.sessions (token_hash,user_id,organization_id,expires_at) VALUES ($1,$2,$3,now()+interval '1 hour')",&[&digest,&user,&organization]).await?;
        // Schema derives exclusively from the validated UUID registration above.
        admin.batch_execute(&format!("CREATE SCHEMA {schema}; CREATE TABLE {schema}.isolation_probe (value text NOT NULL); INSERT INTO {schema}.isolation_probe VALUES ('{index}')")).await?;
        let tenant = Tenant::new(
            organization.to_string(),
            &slug,
            &schema,
            Some(employee),
            "owner",
            false,
        )?;
        fixtures.push((tenant, user, token, digest));
    }
    let outcome:TestResult=async {
        let (a,_,token_a,digest_a)=&fixtures[0]; let (b,_,_,_)=&fixtures[1];
        assert_eq!(verify_session(&db,token_a,a.slug()).await?,Some(a.clone()));
        assert!(verify_session(&db,token_a,b.slug()).await?.is_none());
        let headers=vec![("authorization".into(),format!("bEaReR {token_a}")),("X-SCOUT-ORGANIZATION".into(),a.slug().into())];
        assert_eq!(authenticate_request(&db,"GET","/v1/tasks",&headers).await?,Some(a.clone()));
        assert_eq!(authenticate_request(&db,"GET","/v1/connections",&headers).await?,Some(a.clone()));
        for (method,path) in [("GET","/v1/demo"),("GET","/v1/demolition"),("GET","/v1/connections/google/callback"),("POST","/v1/connections"),("HEAD","/v1/connections")] {
            let error=authenticate_request(&db,method,path,&headers).await.err().ok_or("legacy endpoint allowed")?;
            assert_eq!(error.status,403);assert_eq!(error.code,"not_configured");
        }
        let (ca,cb)=tokio::try_join!(db.scoped(a),db.scoped(b))?;
        let (ra,rb)=tokio::try_join!(ca.query_one("SELECT value,current_schema() FROM isolation_probe",&[]),cb.query_one("SELECT value,current_schema() FROM isolation_probe",&[]))?;
        assert_eq!(ra.get::<_,String>(0),"0"); assert_eq!(rb.get::<_,String>(0),"1");
        assert_eq!(ra.get::<_,String>(1),a.schema_name());assert_eq!(rb.get::<_,String>(1),b.schema_name());
        let worker=load_worker_tenant(&db,a.id()).await?;
        assert_eq!(worker.id(),a.id());assert_eq!(worker.role(),"worker");assert_eq!(worker.employee_id(),a.employee_id());
        assert!(load_worker_tenant(&db,"").await.is_err());assert!(load_worker_tenant(&db,"bad").await.is_err());
        assert!(load_worker_tenant(&db,&Uuid::new_v4().to_string()).await.is_err());
        admin.execute("UPDATE scout_auth.sessions SET expires_at=now()-interval '1 second' WHERE token_hash=$1",&[digest_a]).await?;
        assert!(verify_session(&db,token_a,a.slug()).await?.is_none());
        admin.execute("DELETE FROM scout_auth.memberships WHERE organization_id=$1",&[&Uuid::parse_str(a.id())?]).await?;
        assert!(load_worker_tenant(&db,a.id()).await.is_err());
        Ok(())
    }.await;
    for (tenant, user, _, _) in fixtures {
        admin
            .execute(
                "DELETE FROM scout_auth.organizations WHERE id=$1",
                &[&Uuid::parse_str(tenant.id())?],
            )
            .await?;
        admin
            .execute("DELETE FROM scout_auth.users WHERE id=$1", &[&user])
            .await?;
        admin
            .batch_execute(&format!("DROP SCHEMA {} CASCADE", tenant.schema_name()))
            .await?;
    }
    outcome
}
