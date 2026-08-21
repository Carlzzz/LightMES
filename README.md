# LightMES

Lightweight MES (Manufacturing Execution System) for a notebook shell assembly line.

Python + FastAPI modular monolith. See `docs/superpowers/` for the design spec and implementation plans.

## Production bootstrap

1. Copy `.env.example` to `.env` and replace every password/secret.
2. Run database migrations before starting the app:

   ```powershell
   uv run alembic upgrade head
   ```

3. Create the initial admin account:

   ```powershell
   uv run python scripts/create_admin.py --password "your-strong-password"
   ```

4. Start the API and the data-acquisition listener. In Docker Compose, both
   services are already included; locally run them in separate terminals:

   ```powershell
   uv run uvicorn lightmes.main:app --host 0.0.0.0 --port 8000
   uv run python -m lightmes.modules.connectivity.mqtt_listener
   ```

## Tests

The suite truncates `TEST_DATABASE_URL` on startup. Configure a dedicated test
database before running pytest; LightMES refuses to run tests against the
unspecified development database.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes_test"
uv run pytest
```
