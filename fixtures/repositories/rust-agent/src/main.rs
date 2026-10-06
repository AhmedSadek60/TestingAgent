use axum::{routing::post, Router};

#[tool(description = "Fetch the current price of a stock symbol")]
pub async fn stock_price(symbol: String) -> String { "42".into() }

#[tokio::main]
async fn main() {
    let app = Router::new().route("/ask", post(|| async { "ok" }));
}
