const ui={status:document.querySelector("#system-status"),verifyButton:document.querySelector("#verify-button"),proofResult:document.querySelector("#proof-result"),runButton:document.querySelector("#run-attack-button"),comparison:document.querySelector("#comparison-board"),executionReceipt:document.querySelector("#execution-receipt"),availableBudget:document.querySelector("#available-budget"),availableAmount:document.querySelector("#available-amount"),reservedAmount:document.querySelector("#reserved-amount"),committedAmount:document.querySelector("#committed-amount"),budgetProgress:document.querySelector("#budget-progress"),auditList:document.querySelector("#audit-list"),eventCount:document.querySelector("#event-count"),toast:document.querySelector("#toast")};
const inPaise=rupees=>Math.round(Number(rupees)*100);
const inRupees=paise=>new Intl.NumberFormat("en-IN",{style:"currency",currency:"INR",maximumFractionDigits:paise%100===0?0:2}).format(paise/100);

function readPolicy(prefix="ui-runtime"){
  const monthly=inPaise(document.querySelector("#monthly-limit").value);
  const transaction=inPaise(document.querySelector("#transaction-limit").value);
  const threshold=inPaise(document.querySelector("#approval-threshold").value);
  const hours=Number(document.querySelector("#window-hours").value);
  if(transaction>monthly)throw new Error("Per-transaction limit cannot exceed the monthly budget.");
  if(threshold>monthly)throw new Error("Approval threshold cannot exceed the monthly budget.");
  return{policy_id:`${prefix}-${Date.now()}`,version:1,name:"Autonomous Procurement",currency:"INR",budget:{monthly_limit:monthly,per_transaction_limit:transaction},approval:{required_above:threshold,approver_count:1},vendors:{require_approved_vendor:true,allowed_vendor_ids:["vector-systems"],allowed_categories:["hardware"]},correlation:{window_hours:hours,group_by:["vendor","purpose"]}};
}

function paymentAction(runId,sequence){return{action_id:`${runId}-payment-${sequence}`,agent_id:"procurement-agent",amount:900000,vendor_id:"vector-systems",category:"hardware",purpose:"office-laptops",invoice_id:`${runId}-invoice-${sequence}`,approval_ids:[]}}
async function api(path,options={}){const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});const body=await response.json();if(!response.ok)throw new Error(body.detail||`Request failed with status ${response.status}`);return body}
function setBusy(button,busy,label){if(!button.dataset.label)button.dataset.label=button.innerHTML;button.disabled=busy;button.innerHTML=busy?label:button.dataset.label}
function showToast(message,error=false){ui.toast.textContent=message;ui.toast.classList.toggle("error",error);ui.toast.classList.add("visible");clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>ui.toast.classList.remove("visible"),3600)}

async function checkHealth(){try{await api("/health");ui.status.classList.add("online");ui.status.querySelector("span:last-child").textContent="Runtime online"}catch{ui.status.classList.add("offline");ui.status.querySelector("span:last-child").textContent="Runtime offline"}}

async function verifyPolicy(){setBusy(ui.verifyButton,true,"Searching bounded state space…");try{const result=await api("/api/v1/policies/verify",{method:"POST",body:JSON.stringify({policy:readPolicy("ui-proof"),max_actions:4})});renderProof(result);showToast("Counterexample search completed.")}catch(error){showToast(error.message,true)}finally{setBusy(ui.verifyButton,false)}}

function renderProof(result){
  if(!result.counterexample){ui.proofResult.innerHTML=`<div class="empty-state"><span class="panel-kicker">Bounded result</span><h3>No counterexample found.</h3><p>${result.limitation}</p>${proofEvidence(result)}</div>`;return}
  const attack=result.counterexample;
  const actions=attack.actions.map(action=>`<div class="proof-action"><small>Action ${action.sequence} · locally allowed</small><strong>${inRupees(action.amount)}</strong></div>`).join("");
  ui.proofResult.innerHTML=`<div class="proof-found"><div class="proof-status"><span class="panel-kicker">Solver result</span><span class="danger-chip">Counterexample found</span></div><h3>Approval splitting is possible.</h3><p>${attack.explanation}</p><div class="proof-actions">${actions}</div><div class="proof-total"><span>Correlated commitment</span><strong>${inRupees(attack.correlated_total)}</strong></div>${proofEvidence(result)}</div>`;
}

function proofEvidence(result){const digest=result.evidence_hash||"not recorded";return `<div class="proof-evidence"><div><small>Canonical evidence</small><code>${digest}</code><span>Replay ID ${result.proof_run_id} · bound ${result.checked_bound} actions</span></div><button class="button button-secondary proof-replay" type="button" data-replay-proof="${result.proof_run_id}">Replay proof</button></div><div class="proof-replay-status" aria-live="polite"></div>`}
async function replayProof(button){setBusy(button,true,"Replaying…");try{const replay=await api(`/api/v1/proofs/${encodeURIComponent(button.dataset.replayProof)}/replay`,{method:"POST"});const status=ui.proofResult.querySelector(".proof-replay-status");status.className=`proof-replay-status ${replay.status}`;status.textContent=replay.status==="verified"?"✓ Stored evidence is intact and the solver reproduced the same result.":"⚠ Replay mismatch detected. The stored evidence or result has changed.";showToast(replay.status==="verified"?"Proof replay verified.":"Proof replay mismatch detected.",replay.status!=="verified")}catch(error){showToast(error.message,true)}finally{setBusy(button,false)}}

async function runAttack(){
  setBusy(ui.runButton,true,"Evaluating sequence…");
  try{
    const runId=`run-${Date.now()}`;const policy=readPolicy(runId);
    const first=await api("/api/v1/runtime/evaluate",{method:"POST",body:JSON.stringify({policy,action:paymentAction(runId,1)})});
    const second=await api("/api/v1/runtime/evaluate",{method:"POST",body:JSON.stringify({policy,action:paymentAction(runId,2)})});
    const execution=await api("/api/v1/executions/orders",{method:"POST",body:JSON.stringify({policy_id:policy.policy_id,policy_version:1,action_id:first.action_id})});
    const confirmation=execution.order.mode==="simulate"
      ? await api("/api/v1/executions/confirm",{method:"POST",body:JSON.stringify({policy_id:policy.policy_id,policy_version:1,action_id:first.action_id,simulated_outcome:"success"})})
      : await confirmWithRazorpay(execution,policy,first.action_id);
    const state=await api(`/api/v1/runtime/policies/${encodeURIComponent(policy.policy_id)}/state?version=1`);
    renderComparison([first,second]);renderExecution(execution,confirmation);renderRuntime(state,policy.budget.monthly_limit);showToast("Payment verified and reservation committed.");
  }catch(error){showToast(error.message,true)}finally{setBusy(ui.runButton,false)}
}

function decisionClass(decision){if(decision==="allow"||decision==="allow_and_reserve")return"allow";if(decision==="require_approval")return"review";return"deny"}
function decisionLabel(decision){return decision.replaceAll("_"," ")}
function renderComparison(results){
  const rows=results.map((result,index)=>`<div class="comparison-row"><div><small>Request</small><strong>0${index+1} · ${inRupees(900000)}</strong></div><div><span class="decision ${decisionClass(result.naive_gateway.decision)}">${decisionLabel(result.naive_gateway.decision)}</span><span class="reason-text">${result.naive_gateway.explanation}</span></div><div><span class="decision ${decisionClass(result.arthaniyam.decision)}">${decisionLabel(result.arthaniyam.decision)}</span><span class="reason-text">${result.arthaniyam.explanation}</span></div><div><small>Correlated total</small><strong>${inRupees(result.correlated_amount)}</strong></div></div>`).join("");
  ui.comparison.innerHTML=`<div class="comparison-head"><span>Request</span><span>Naive gateway</span><span>ArthaNiyam</span><span>Shared state</span></div>${rows}`;
}

function renderExecution(execution,confirmation){const order=execution.order;ui.executionReceipt.classList.add("executed");ui.executionReceipt.innerHTML=`<span class="receipt-icon">✓</span><div><small>${order.provider} · ${order.mode} mode · ${confirmation.status.replaceAll("_"," ")}</small><strong>Verified payment ${confirmation.payment_id} for ${inRupees(order.amount)}</strong></div><code>${order.order_id}</code>`}

async function loadRazorpayCheckout(){if(window.Razorpay)return;await new Promise((resolve,reject)=>{const script=document.createElement("script");script.src="https://checkout.razorpay.com/v1/checkout.js";script.onload=resolve;script.onerror=()=>reject(new Error("Razorpay Checkout could not be loaded."));document.head.appendChild(script)})}
async function confirmWithRazorpay(execution,policy,actionId){await loadRazorpayCheckout();const checkoutResponse=await new Promise((resolve,reject)=>{const checkout=new window.Razorpay({key:execution.checkout_key_id,amount:execution.order.amount,currency:"INR",name:"ArthaNiyam",description:"Policy-approved autonomous payment",order_id:execution.order.order_id,theme:{color:"#11120f"},handler:resolve,modal:{ondismiss:()=>reject(new Error("Razorpay Test Checkout was dismissed."))}});checkout.open()});return api("/api/v1/executions/confirm",{method:"POST",body:JSON.stringify({policy_id:policy.policy_id,policy_version:1,action_id:actionId,razorpay_payment_id:checkoutResponse.razorpay_payment_id,razorpay_order_id:checkoutResponse.razorpay_order_id,razorpay_signature:checkoutResponse.razorpay_signature})})}

function renderRuntime(runtimeState,monthlyLimit){
  const state=runtimeState.state;const used=state.reserved_amount+state.committed_amount;const percent=monthlyLimit?Math.min(100,used/monthlyLimit*100):0;
  ui.availableBudget.textContent=`${inRupees(state.available_budget)} available`;ui.availableAmount.textContent=inRupees(state.available_budget);ui.reservedAmount.textContent=inRupees(state.reserved_amount);ui.committedAmount.textContent=inRupees(state.committed_amount);ui.budgetProgress.style.width=`${percent}%`;ui.eventCount.textContent=`${runtimeState.audit_trail.length} events`;
  ui.auditList.innerHTML=runtimeState.audit_trail.slice().reverse().map(event=>`<div class="audit-event"><time>${new Date(event.occurred_at).toLocaleTimeString("en-IN")}</time><div><strong>${decisionLabel(event.decision)}</strong><br/><span>${event.reason_codes.join(" · ")}</span></div><code>${event.event_id.slice(0,8)}</code></div>`).join("");
}

ui.verifyButton.addEventListener("click",verifyPolicy);ui.runButton.addEventListener("click",runAttack);ui.proofResult.addEventListener("click",event=>{const button=event.target.closest("[data-replay-proof]");if(button)replayProof(button)});checkHealth();
