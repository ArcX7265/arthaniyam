# ArthaNiyam — 90-second support demo

## Setup

Keep Ollama running with llama3.2:3b. From the repository root:

```powershell
# A new database preserves existing demo history.
$env:ARTHANIYAM_DATABASE_PATH = Join-Path $env:TEMP ("arthaniyam-recording-" + [guid]::NewGuid().ToString() + ".sqlite3")
.\scripts\start-demo.ps1 -Port 8011 -InvestigatorMode ollama
```

Open http://127.0.0.1:8011/. The scenario shortcuts seed payments and prefill a request; they do not submit or approve it. Reusing a database preserves refunded balances; repeating a full refund may correctly be blocked.

## Recording script

| Time | Show | Say |
| --- | --- | --- |
| 0–12s | Inbox, expand How it works | “ArthaNiyam investigates merchant refund complaints and follows approved requests to their recorded outcome.” |
| 12–25s | Duplicate · ₹1,250 shortcut, review, submit | “The operator selects payment and amount. Local Llama reads the complaint and evidence, then proposes a resolution.” |
| 25–40s | Loading, then proposal and tools | “The model investigates; financial authority stays with server checks. Inference runs locally.” |
| 40–55s | Confirm proposal, pending, synthetic receipt | “Confirmation runs eligibility and cumulative-refund checks. A synthetic receipt completes the case.” |
| 55–70s | Prepared ₹7,500 case awaiting approval | “Confirming intent is separate from finance approval, which is bound to this evidence.” |
| 70–80s | Prepared handoff case | “Recognised attempts to bypass checks go to human review before inference.” |
| 80–90s | Evidence timeline and limitations | “This is a local simulator. Next are trusted merchant roles, provider reconciliation and broader language evaluation.” |

CPU requests previously took about 16–27 seconds. Prepare the cancellation and handoff cases first for a strict 90-second recording. Label any edit that skips inference waiting; do not report edited video as a latency measurement. A continuous walkthrough may take 2–3 minutes.

## Acceptance checks

1. Duplicate: proposal -> confirmation -> refund pending -> synthetic receipt.
2. Status: after that receipt, submit “Where is my refund?” without an amount; confirm the status proposal. No new refund.
3. Handoff: bypass shortcut -> Needs review, with no proposal or refund.
4. Cancellation: ₹7,500 -> proposal -> confirmation -> Awaiting approval -> Approve demo refund -> receipt.

Automated API equivalent, using a disposable database:

```powershell
.\.venv\Scripts\python.exe scripts/verify-support-demo.py --mode ollama
```

This check confirms only simulated refunds in the temporary database. The separate investigator evaluator never confirms proposals.

## Capture plan

Capture desktop images at roughly 1280–1440 pixels wide, browser zoom 100%:

- screenshots/01-workspace.png — inbox, How it works expanded, shortcuts and metrics.
- screenshots/02-evidence-proposal.png — duplicate complaint, proposal, evidence and tool trace.
- screenshots/03-finance-review.png — cancelled-order case awaiting approval.
- screenshots/04-receipt.png — resolved duplicate and receipt timeline.

Then record the script with the Windows screen recorder. Use synthetic complaints only. Screenshots, video and browser visual QA remain pending: browser automation was blocked by the approval reviewer's usage limit. Do not present generated mockups as screenshots.

## Technical follow-up

Open /labs for the earlier split-payment comparison and downloadable evidence. Its bounded solver assumptions are separate from the support workflow. The comparison is a deliberately simplified baseline, not Paytm's internal controls.
