"""Jobs used for CLI integration tests."""

from soniq import Soniq

app = Soniq()


@app.job(name="cli_fixture_job")
async def cli_fixture_job(message: str = "hello"):
    return f"cli:{message}"
