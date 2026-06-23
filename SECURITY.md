# Security Policy

## Supported Branch

`main` is the only maintained branch for this personal project.

## Secrets

Do not commit API keys, service account keys, `.env` files, downloaded credentials, or BigQuery export files that contain private operational data.

Production automation uses:

- `ODPT_API_KEY` as a GitHub Actions secret
- `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT` as repository variables
- GitHub Actions Workload Identity Federation for Google Cloud authentication

Service account JSON keys should not be created for this repository. If a key or API token is exposed, revoke it immediately and rotate the GitHub Secret or Google Cloud credential.

## Dependency Updates

Dependabot checks Python and GitHub Actions dependencies weekly. Dependency PRs should pass CI before merging. If an automated update fails because a test is too version-specific, loosen the test only when the behavior remains covered.

## Reporting Issues

For data corrections, use the data correction issue template. For failed updates, stale pages, or suspected credential exposure, open an incident issue and include the relevant GitHub Actions run URL.

This project displays statistical reference values, not official weather forecasts or airline operation decisions.
