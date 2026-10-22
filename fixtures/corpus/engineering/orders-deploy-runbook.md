---
title: Orders Service Deployment Runbook
owner: platform-team
---
# Orders Service Deployment Runbook

## Prerequisites

You need membership of the `deployers` group and a green build of `orders-svc` on the main branch.
The deployment manifest lives in `deploy-orders.yaml` in the platform repository.

## Rolling deployment

Deployments use a rolling strategy with a maximum of 25% of pods unavailable. Start the rollout with
`hctl deploy orders-svc --version v3.8.2` (substitute the version). Watch the error rate dashboard for
ten minutes after the rollout finishes.

## Rollback

If the error rate exceeds 1% or p95 latency doubles, roll back immediately with
`hctl rollback orders-svc`. Rollback restores the previous ReplicaSet and does not revert database
migrations; migrations must therefore always be backwards compatible for one release.

## Database migrations

Run migrations before deploying code that depends on them. Never drop a column in the same release
that stops reading it.
