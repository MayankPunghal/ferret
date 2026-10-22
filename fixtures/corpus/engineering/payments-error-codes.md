---
title: Payments Service Error Code Reference
owner: payments-team
---
# Payments Service Error Code Reference

This reference lists error codes returned by `payments-svc` and what operators should do.

## ERR-PAY-4012: Card authorization timeout

Returned when the upstream card processor does not answer within 8 seconds. The client may retry
once with the same idempotency key. If more than 2% of authorizations fail with ERR-PAY-4012 over
five minutes, page the payments on-call engineer and check the processor status page.

## ERR-PAY-4020: Duplicate idempotency key

The request reused an idempotency key with a different payload. This is a client bug; do not retry.
Log the `request_id` and contact the integrating team.

## ERR-PAY-5001: Ledger write failed

The payment was authorized but the ledger write failed. The ledger consumer retries automatically
using `ledger.retry_backoff_ms` (default 250 ms, doubling up to 8 attempts). Do not refund manually
until the reconciliation job has run, otherwise the customer may be refunded twice.
