# Anomaly Detection

The Anomaly Engine runs independently of the API. It evaluates `Position` updates to detect:

- **Position Stale**: Position unchanged for >15 minutes.
- **Signal Loss**: No updates received for >15 minutes.
- **Impossible Jump**: Geographic displacement exceeding threshold.
- **Inconsistencies**: Impossible speeds, heading jumps, or altitude changes.

Findings generate `Alert` objects (severity: NORMAL, WATCH, WARNING, CRITICAL) stored in PostgreSQL.
