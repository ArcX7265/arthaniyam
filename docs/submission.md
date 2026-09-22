# ArthaNiyam — Paytm hackathon submission draft

## Track

Autonomous AI Teammates, based on the supplied track description. Confirm official eligibility, reuse rules, deadlines and mandatory integrations before submission.

## One-line pitch

An AI teammate that investigates merchant refund complaints, gathers evidence, asks for missing details and carries approved requests through a tracked simulated refund workflow.

## Problem

A merchant handling a complaint must connect the customer's account with order, capture and refund history. The proposed remedy must fit captured funds, earlier refunds and approval rules. Repeated or concurrent requests make those checks harder.

## What we built

Local Llama 3.2 reads scoped evidence and proposes a complaint classification, clarification or handoff. The operator supplies the payment and amount and confirms the proposal. Server checks decide whether a simulated refund is eligible, blocked or requires separate finance approval. A durable worker follows accepted refunds to synthetic receipts.

The minimal inbox shows complaints, evidence, proposed next steps and event history. Four shortcuts demonstrate duplicate payments, cancelled-order approval, refund tracking and human handoff. The earlier policy demonstrations remain at /labs.

## Distinctive features

- Limits consider earlier and pending refunds against the same capture, including split requests.
- Approval is tied to exact evidence; changes or expiry force another review.
- Model tools cannot execute refunds or grant approval.
- Idempotency and atomic SQLite admission prevent repeated confirmation from creating duplicate refunds.
- Local inference avoids paid API calls. Recognised status enquiries and explicit safeguard-bypass wording use rules.

## Evidence and limits

See [validation results](validation.md) for dated measurements. The core development suite passed seven fixtures, three without inference. New Hindi/Hinglish evaluation passed three of four examples; Hindi refund tracking still needs work. These are not independent production-accuracy measurements.

Payments and receipts are simulated. There is no Paytm API integration, trusted operator authentication or merchant isolation. Finance approval is a simulated role. The narrow regex gate is not a general prompt-injection defence; deterministic financial controls remain necessary.

## Intended impact

Less manual evidence collection, faster triage and fewer repeated refund actions are hypotheses to validate with merchants. A pilot should measure resolution time, reviewer corrections, escalation rate, repeat contacts and incorrect actions against a human-only baseline. No customer-impact claim has been measured yet.

## Scaling plan

Add authenticated tenant scope and roles, a production transactional database, durable queues, provider idempotency/reconciliation, observability and unseen multilingual evaluations. Pilot with an authorised provider sandbox before real-money use. Current concurrency tests cover shared SQLite, not multi-host consensus.

## Fields to complete before submission

Team names, contact details, disclosure of pre-existing ArthaNiyam work, confirmed public repository URL, video URL and any hosted-demo link. No public deployment, push or submission was performed. Use [the demo script](judge-demo.md) and [checklist](submission-checklist.md).
