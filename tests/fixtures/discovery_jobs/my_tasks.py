"""Test jobs for the SONIQ_JOBS_MODULES discovery mechanism.

The deploy-time pattern is: define a Soniq instance once at the module
top, decorate jobs against it, and point ``SONIQ_JOBS_MODULES`` at this
module. Importing the module registers the jobs on ``app``.
"""

from soniq import Soniq

app = Soniq()


@app.job(name="discovered_job")
async def discovered_job(message: str):
    return f"Processed: {message}"
