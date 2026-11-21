#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    scout_dashboard::server::run().await
}
