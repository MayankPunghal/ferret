---
title: Data Platform Overview
owner: data-team
---
# Data Platform Overview

The data platform ingests events from product services into the warehouse every 15 minutes. Event
schemas are registered in the schema registry; a breaking schema change requires a new topic version.

## Retention

Raw events are retained for 400 days. Aggregated tables are retained indefinitely. Personal data
fields are pseudonymized at ingestion using a keyed hash.

## Access

Analysts request warehouse access through the access portal. Access to tables with personal data
requires approval from the data protection officer.
