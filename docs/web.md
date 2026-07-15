# Web application

The Flask application renders an 11-day statistical reference for the three daily ANA flights from Haneda to Hachijojima. The displayed percentages are uncalibrated statistical reference values, not validated probabilities.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
flask --app web_app run
```

Open <http://127.0.0.1:5000/>. BigQuery Application Default Credentials are required because operational history is read only from BigQuery.

## Static build and GitHub Pages

```bash
python build_static.py
```

The `Deploy forecast site to Pages` workflow builds `dist/` every six hours and deploys it to GitHub Pages. The displayed update time is the acquisition time of the forecast bundle actually in use. A main forecast cache is accepted only when it is at most seven hours old.

## Dynamic deployment

The included `Procfile` starts the Flask application with Gunicorn:

```text
web: gunicorn web_app:app
```

Use `/health` as the health-check path. Store no database dump or service-account key in the deployment artifact.
