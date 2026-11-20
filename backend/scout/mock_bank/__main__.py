import os

import uvicorn

uvicorn.run("scout.mock_bank.app:app", host=os.environ.get("BANK_HOST", "127.0.0.1"),
            port=int(os.environ.get("BANK_PORT", "8010")))
