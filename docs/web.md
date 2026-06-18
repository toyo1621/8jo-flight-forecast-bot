# Web application

The Flask application shows a seven-day forecast for the three daily ANA flights
from Haneda to Hachijojima.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app web_app run
```

Open <http://127.0.0.1:5000/>.

The application restores `flights.db` from `data/flights_dump.sql` when the local
database does not exist. It then retrieves the current seven-day weather forecast
from Open-Meteo. No API key is required for the web page.

## Deploy

The included `Procfile` starts the application with Gunicorn:

```text
web: gunicorn web_app:app
```

Configure the hosting platform to install `requirements.txt` and use `/health` as
its health-check path.

## GitHub Pages

The `Deploy forecast site to Pages` workflow builds a static copy of the forecast
every three hours and deploys `dist/` to GitHub Pages. In the repository settings,
select **Settings > Pages > Build and deployment > Source > GitHub Actions** once.
