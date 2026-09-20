"use strict";
// Untrusted customer/API strings are rendered only as text, never HTML.
const $ = (id) => document.getElementById(id);
const labels = {new: "New", investigating: "Investigating", waiting_information: "Needs information", proposal_ready: "Review proposal", needs_review: "Needs review", awaiting_approval: "Awaiting approval", refund_pending: "Refund pending", resolved: "Resolved", human_owned: "Human owned", blocked: "Blocked"};
const kinds = {duplicate_payment: "Duplicate payment", cancelled_order: "Cancelled order", refund_request: "Return / partial refund", refund_status: "Refund status", other: "Other complaint"};
const money = (paise) => new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR"}).format(paise / 100);
let cases = [], payments = [], selected = null, busy = false, refreshing = false, retry = null;
let listSignature = "", detailSignature = "";
let dialogCaseId = null;
function caseTitle(c) {return c.intake === "investigator" && c.request.kind === "other" ? "Complaint investigation" : kinds[c.request.kind];}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
async function api(path, body) {
  const response = await fetch(`/api/v1/support${path}`, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request is invalid. Check the fields and try again.");
  return data;
}
function badge(status) {return node("span", labels[status] || status, `badge ${status}`);}
function renderList() {
  const query = $("search").value.toLowerCase();
  const signature = JSON.stringify([cases, selected, query]);
  if (signature === listSignature) return;
  listSignature = signature;
  const list = $("requests"); list.replaceChildren();
  const filtered = cases.filter(c => `${c.case_id} ${c.request.message} ${c.request.payment_id} ${labels[c.status]} ${kinds[c.request.kind]}`.toLowerCase().includes(query));
  if (!filtered.length) list.append(node("p", cases.length ? "No matching requests." : "No requests yet. Start with a demo payment."));
  for (const c of filtered) {
    const button = node("button", undefined, "case-button"); button.type = "button";
    button.setAttribute("aria-pressed", String(c.case_id === selected));
    button.append(node("strong", caseTitle(c)), badge(c.status), node("small", `${c.request.payment_id || "Payment not selected"} · ${c.request.amount ? money(c.request.amount) : "No refund amount"}`));
    button.onclick = () => {selected = c.case_id; renderList(); renderDetail();};
    list.append(button);
  }
}
function action(label, handler, quiet = false) {
  const button = node("button", label, quiet ? "quiet" : ""); button.type = "button"; button.disabled = busy;
  button.onclick = () => perform(handler); return button;
}
function renderDetail() {
  const c = cases.find(item => item.case_id === selected);
  if (!c) return;
  const signature = JSON.stringify([c, busy]);
  if (signature === detailSignature) return;
  detailSignature = signature;
  const detail = $("detail"); detail.replaceChildren();
  const heading = node("div", undefined, "detail-heading"), title = node("div");
  title.append(node("p", c.case_id, "case-id"), node("h2", caseTitle(c)));
  heading.append(title, badge(c.status)); detail.append(heading, node("p", c.request.message, "complaint"));
  if (c.intake === "investigator") {
    detail.append(node("p", c.mode === "openai_investigator" ? "OpenAI investigator · financial authority remains with the server." : "Offline reference investigator · keyword-based, not a live model."));
    if (c.status === "investigating") detail.append(node("p", "Investigation is running (up to 45 seconds). If interrupted, retry after one minute; persisted run claims prevent duplicate work."));
    for (const message of c.messages || []) detail.append(node("p", `Follow-up: ${message.message}`, "complaint"));
    if (c.investigation) {
      const info = c.investigation;
      detail.append(node("h3", info.mode === "openai" ? "AI investigation" : "Reference investigation"));
      detail.append(node("p", info.details.question || info.details.reason || info.details.rationale));
      if (info.tools.length) detail.append(node("p", `Tools used: ${info.tools.map(item => item.tool.replaceAll("_", " ")).join(" → ")}`, "case-id"));
    }
  }
  if (c.evidence) {
    const evidence = c.evidence, facts = node("div", undefined, "facts");
    const values = [["Captured", money(evidence.captured_amount)], ["Refunded / in progress at review", money(evidence.refunded_or_pending)], ["Available at review", money(evidence.remaining)], ["Cumulative approval threshold", money(evidence.policy.approval_above)], ["Order", evidence.payment.order_id], ["Capture verified", evidence.captured ? "Yes · simulator" : "No"]];
    for (const [label, value] of values) {const fact = node("div", undefined, "fact"); fact.append(node("span", label), node("strong", value)); facts.append(fact);}
    detail.append(facts);
  }
  if (c.refund) detail.append(node("p", `${money(c.refund.amount)} · ${c.refund.confirmed ? "Synthetic receipt confirmed" : "Accepted, awaiting synthetic receipt"} · ${c.refund.provider_refund_id}`));
  const actions = node("div", undefined, "actions");
  if (c.status === "proposal_ready" && c.proposal && !c.human_owned) {
    detail.append(node("p", `Proposed: ${kinds[c.proposal.kind]} · ${c.proposal.payment_id} · ${c.proposal.kind === "refund_status" ? "status check only" : money(c.proposal.amount)}. Confirm only if this matches your intent. Eligibility and finance approval are checked next.`));
    actions.append(action("Confirm proposal & run checks", async () => {
      const updated = await api(`/requests/${c.case_id}/confirm-proposal`, {proposal_id: c.proposal.proposal_id});
      $("message").textContent = updated.status === "needs_review" ? "The request needs review. See the activity timeline." : "Intent confirmed. Server checks determined the next step.";
    }));
  }
  if (c.intake === "investigator" && !c.human_owned && !c.refund && !["resolved", "awaiting_approval"].includes(c.status)) {
    actions.append(action("Add information", () => openForm(c), true));
  }
  if (c.status === "awaiting_approval" && !c.human_owned) {
    detail.append(node("p", "Review this amount and evidence before approving. This demo approval expires after five minutes."));
    actions.append(action("Approve demo refund", async () => {
      const updated = await api(`/requests/${c.case_id}/approve`, {evidence_fingerprint: c.evidence_fingerprint, reviewer: "demo-finance-reviewer"});
      $("message").textContent = updated.status === "needs_review" ? "Approval invalidated. Review the refreshed evidence." : "Demo approval recorded. Tracking the refund.";
    }));
  }
  if (["new", "investigating", "waiting_information", "proposal_ready", "needs_review", "blocked", "awaiting_approval"].includes(c.status) && !c.human_owned && !(c.intake === "investigator" && c.status === "awaiting_approval")) actions.append(action("Investigate again", () => api(`/requests/${c.case_id}/investigate`, {}), true));
  if (!c.human_owned && c.status !== "resolved") actions.append(action("Take over", async () => {
    if (window.confirm("Pause automation and assign this request to a human? Already submitted refunds cannot be cancelled.")) await api(`/requests/${c.case_id}/takeover`, {reason: "Operator requested manual review from the support inbox."});
  }, true));
  if (actions.childElementCount) detail.append(actions);
  detail.append(node("h3", "Evidence & activity"));
  const timeline = node("ol", undefined, "timeline");
  for (const event of c.timeline) {const item = node("li"); const at = node("time", new Date(event.at * 1000).toLocaleString()); at.dateTime = new Date(event.at * 1000).toISOString(); item.append(at, node("span", event.message)); timeline.append(item);}
  detail.append(timeline);
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const [loaded, metrics] = await Promise.all([api("/requests"), api("/metrics")]); cases = loaded;
    if (!selected && cases.length) selected = cases[0].case_id;
    const panel = $("metrics"); panel.replaceChildren();
    for (const [value, label] of [[metrics.total, "Requests"], [metrics.resolved, "Resolved requests"], [metrics.awaiting_approval, "Awaiting approval"], [money(metrics.confirmed_refund_paise), "Confirmed simulated refunds"]]) {const card = node("div", undefined, "metric"); card.append(node("strong", String(value)), node("span", label)); panel.append(card);}
    renderList(); renderDetail();
    if ($("message").textContent.startsWith("Refresh failed:")) $("message").textContent = "Connection restored.";
  } finally {refreshing = false;}
}
async function perform(handler) {
  if (busy) return;
  busy = true; renderDetail();
  try {await handler(); await refresh();} catch (error) {$("message").textContent = error.message;} finally {busy = false; renderDetail();}
}
async function loadPayments() {
  payments = await api("/payments"); $("payment").replaceChildren();
  const unknown = node("option", "Not sure — ask me for the payment"); unknown.value = ""; $("payment").append(unknown);
  for (const payment of payments) {const option = node("option", payment.label); option.value = payment.payment_id; $("payment").append(option);}
}
$("load-demo").onclick = () => perform(async () => {await api("/demo/seed", {}); await loadPayments(); $("message").textContent = "Four demo payments ready. Create a duplicate-payment, cancellation or return request.";});
$("new-request").onclick = async () => {
  try {await openForm();} catch (error) {$("message").textContent = error.message;}
};
async function openForm(c = null) {
  await loadPayments();
  if (!payments.length) {$("message").textContent = "Load demo payments first."; return;}
  $("request-form").reset(); dialogCaseId = c ? c.case_id : null;
  $("form-title").textContent = c ? "Add missing information" : "New support request";
  $("submit-request").textContent = c ? "Send information & investigate" : "Investigate request";
  $("intake-mode").hidden = Boolean(c); $("intake-label").hidden = Boolean(c);
  $("guided-fields").hidden = true;
  if (c) {$("payment").value = c.request.payment_id || ""; $("amount").value = c.request.amount ? (c.request.amount / 100).toFixed(2) : "";}
  $("form-error").textContent = ""; $("request-dialog").showModal();
}
$("intake-mode").onchange = () => {$("guided-fields").hidden = $("intake-mode").value !== "guided";};
$("close-dialog").onclick = () => $("request-dialog").close();
$("search").oninput = renderList;
$("refresh").onclick = () => perform(refresh);
$("request-form").onsubmit = async event => {
  event.preventDefault(); if (busy) return;
  const raw = $("amount").value.trim();
  let amount = null;
  if (raw) {const [rupees, paise = ""] = raw.split("."); amount = Number(rupees) * 100 + Number(paise.padEnd(2, "0")); if (!Number.isSafeInteger(amount) || amount <= 0 || amount > 100000000) {$("form-error").textContent = "Enter a positive INR amount up to 10,00,000."; return;}}
  const guided = !dialogCaseId && $("intake-mode").value === "guided";
  const body = {payment_id: $("payment").value || null, amount, message: $("complaint").value.trim()};
  if (guided) {
    body.kind = $("kind").value;
    if (!body.payment_id) {$("form-error").textContent = "Select a payment for the guided workflow."; return;}
    if (["duplicate_payment", "cancelled_order", "refund_request"].includes(body.kind) && !amount) {$("form-error").textContent = "Enter the refund amount."; return;}
  }
  const endpoint = dialogCaseId ? `/requests/${dialogCaseId}/messages` : guided ? "/requests" : "/investigations";
  const fingerprint = JSON.stringify([endpoint, body]);
  if (!retry || retry.fingerprint !== fingerprint) retry = {fingerprint, key: crypto.randomUUID()};
  busy = true; const submit = event.target.querySelector("button[type=submit]"); submit.disabled = true;
  $("form-error").textContent = "Investigating… This can take up to 45 seconds in OpenAI mode.";
  try {
    const result = await api(endpoint, {...body, idempotency_key: retry.key});
    selected = result.case_id; retry = null; $("request-dialog").close(); $("request-form").reset();
    $("message").textContent = "Request updated. Review its evidence and next action."; await refresh();
  } catch (error) {$("form-error").textContent = error.message;} finally {busy = false; submit.disabled = false; renderDetail();}
};
refresh().catch(error => {$("message").textContent = error.message;});
api("/investigator/capabilities").then(cap => {
  const mode = cap.mode === "openai" ? `OpenAI investigator (${cap.model})${cap.configured ? "" : " — API key not configured"}` : "Offline reference investigator — no live model";
  $("investigator-mode").textContent = `${mode}. Synthetic payments only; no real money moves. Demo approvals are not authenticated finance access.`;
  $("data-notice").textContent = cap.mode === "openai" ? "AI mode sends this complaint, follow-up messages and selected synthetic payment evidence to OpenAI. Use demo data only. Guided mode stays offline." : "Investigation uses an offline keyword reference. Configure OpenAI on the server for model-based understanding.";
}).catch(error => {$("investigator-mode").textContent = `Could not check investigator configuration: ${error.message}`;});
setInterval(() => {if (!busy && !document.hidden && !$("request-dialog").open) refresh().catch(error => {$("message").textContent = `Refresh failed: ${error.message}`;});}, 3000);
