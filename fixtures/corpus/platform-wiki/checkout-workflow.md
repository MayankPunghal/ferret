---
title: Checkout Workflow
related: [orders-service, payments-service]
---
# Checkout Workflow

1. The storefront submits the basket to the [[Orders Service]].
2. Orders asks the [[Payments Service]] to authorize the card.
3. Both services record entries in the [[Ledger Service]].
