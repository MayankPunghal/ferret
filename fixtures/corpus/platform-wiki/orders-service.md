---
title: Orders Service
depends_on: [payments-service, ledger-service]
part_of: checkout-workflow
---
# Orders Service

`orders-svc` accepts orders from the storefront and reserves stock. It calls the
[[Payments Service]] to authorize cards and writes order totals to the [[Ledger Service]].

## Operations

Deployments and rollbacks follow the [Orders deployment runbook](orders-deploy-runbook.md#rollback).
