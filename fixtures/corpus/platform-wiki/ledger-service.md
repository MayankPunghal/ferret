---
title: Ledger Service
---
# Ledger Service

The ledger consumer writes double-entry records for every payment and order. Retry and batch
settings are documented in the [ledger configuration notes](ledger-config.txt).

## Schema changes

Schema changes to the ledger tables affect every service that writes to the ledger.
