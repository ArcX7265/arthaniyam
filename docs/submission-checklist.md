# Submission checklist

## Repository — complete

- [x] Clear README and one-command Windows startup
- [x] Container and Compose configuration
- [x] GitHub Actions verification workflow
- [x] `.env` and generated databases excluded from Git
- [x] Live Razorpay keys rejected by application settings
- [x] Architecture and trust-boundary documentation
- [x] Judge-facing submission narrative
- [x] Judge Q&A and technical defense sheet
- [x] Automated tests and reproducible evidence

## Demo — complete in product

- [x] One-click 90-second guided flow
- [x] Local gateway versus ArthaNiyam comparison
- [x] Concrete correlated split-payment result
- [x] One-click judge scorecard
- [x] Portable evidence download and standalone verifier
- [x] Honest limitations displayed with results

## External submission tasks — pending

- [ ] Confirm the official submission deadline and field limits
- [ ] Add team-member names and contact details
- [ ] Choose the final public GitHub repository visibility
- [ ] Push and confirm the GitHub Actions workflow is green
- [ ] Start Docker Desktop and confirm `docker compose up --build`
- [ ] Choose a deployment host or state clearly that the project runs locally
- [ ] Record a 90–120 second demo video
- [ ] Capture one hero screenshot and one judge-scorecard screenshot
- [ ] Add the public repository, deployment, and video links to the submission
- [ ] Rehearse answers about bounded proof, synthetic metrics, and scaling

## Final claim check

Use these precise statements:

- “ArthaNiyam found and blocked every attack in our included synthetic suite.”
- “The fixed suite measured 100% attack recall and 0% false positives on its
  four benign controls.”
- “Twelve independently locked instances preserved a shared INR 50,000 budget.”
- “The counterexample search is bounded to the configured number of actions.”
- “The prototype rejects live payment keys and uses simulation or Test Mode.”

Avoid claiming:

- Production fraud accuracy
- Unbounded formal correctness
- Multi-host distributed safety
- Regulatory certification
- Production-ready human identity or authorization
