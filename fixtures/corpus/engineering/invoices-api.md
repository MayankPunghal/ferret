---
title: Invoices API v2 Reference
owner: billing-team
---
# Invoices API v2 Reference

Base URL: `https://api.halvorsen.example/api/v2`. All endpoints require an OAuth bearer token with
the `invoices:write` or `invoices:read` scope.

## Create an invoice

`POST /api/v2/invoices` creates a draft invoice. Line items are validated against the price book;
amounts are integers in minor currency units.

## Void an invoice

`POST /api/v2/invoices/{id}/void` voids an issued invoice. Only invoices in the `issued` state can be
voided; paid invoices must be credited instead with a credit note. Voiding is irreversible and is
recorded in the audit log with the caller's client id.

## Rate limits

Each client may send 300 requests per minute. Exceeding the limit returns HTTP 429 with a
`Retry-After` header. Bulk exports should use the nightly export instead of paging the API.
