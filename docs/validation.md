# Validation checkpoint — 21 September 2026

## Automated regression checks

The backend suite passed all 138 tests after restoring the served `frontend/app.js` from the existing root file. Both frontend scripts passed Node syntax checking. Investigator tests use mock transports and verify safety contracts, not model quality. The restored file retains the earlier labs implementation; this is not a claim that its legacy dynamic rendering was hardened.

## Core investigation evaluation

The prior live run with local `llama3.2:3b` passed 7/7 synthetic development fixtures. Four used model inference; status-only, missing-payment and recognised bypass cases were handled before inference. No proposals were confirmed by that evaluator. The regex preflight is narrow and has not established general injection resistance.

## Added language evaluation

Command: `.\.venv\Scripts\python.exe scripts/evaluate-investigator.py --mode ollama --suite languages`

| Example | Expected | Observed | Result | Seconds |
| --- | --- | --- | --- | --- |
| Hinglish duplicate complaint | Duplicate proposal | Duplicate proposal | Pass | 23.91 |
| Hinglish cancelled order | Cancellation proposal | Cancellation proposal | Pass | 15.27 |
| Hindi “मेरा रिफंड कब आएगा?” | Status proposal | Asked for information | Fail | 23.84 |
| Hindi duplicate complaint | Duplicate proposal | Duplicate proposal | Pass | 27.19 |

Result: 3/4 on this small set. No prompt or fixture was changed to hide the failure. This establishes neither broad Hindi support nor production accuracy. Expand unseen language, mixed-intent, negation, follow-up and adversarial cases before making quality claims.

## End-to-end walkthrough

Result: **4/4 workflows passed** in the current local Ollama run. The duplicate and cancellation paths recorded a total of 875,000 paise (₹8,750) in confirmed simulated refunds. The status check created no additional refund, and the handoff created neither a proposal nor a refund.

`scripts/verify-support-demo.py --mode ollama` checks the four support workflows through the app's HTTP interface, including actual local inference for duplicate/cancellation, operator confirmation, separate simulated finance approval, idempotent confirmation and the real background synthetic-receipt worker. It uses a fresh temporary database, which it removes after execution. Unlike the investigator evaluator, it intentionally confirms simulated refunds.

## Unverified in this run

Browser visual QA, screenshots and recording are pending because automatic browser approval failed with a usage-limit message. Docker, public deployment, provider integrations, event eligibility and submission were not verified or performed. No production money moved.
