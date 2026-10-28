
const $=id=>document.getElementById(id), fmt=n=>new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(n);
let token=sessionStorage.getItem('finpilot-token')||'';$('token').value=token;
function notify(message, error = false) {
  const notice = $('notice');
  notice.replaceChildren(text('span', message));
  notice.classList.toggle('alert', error);
  notice.setAttribute('role', error ? 'alert' : 'status');
  const close = text('button', '×');
  close.type = 'button'; close.setAttribute('aria-label', 'Dismiss message');
  close.addEventListener('click', () => { notice.style.display = 'none'; });
  notice.append(close); notice.style.display = 'flex';
}
async function api(path,body,method){const headers={};if(token)headers.Authorization='Bearer '+token;if(body&&!(body instanceof FormData))headers['Content-Type']='application/json';const response=await fetch('/api/'+path,{method:method||(body?'POST':'GET'),headers,body:body instanceof FormData?body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Check your input and try again.');return data}
function text(tag,value,cls){const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el}
function draw(path) {
  const svg = $('chart'); svg.replaceChildren();
  if (!path || path.length < 2) return;
  const ns = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, content) {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
    if (content !== undefined) node.textContent = content;
    return node;
  }
  let low = Math.min(...path.map(p => p.balance)), high = Math.max(...path.map(p => p.balance));
  const spread = Math.max(high - low, 1000);
  low = low >= 0 ? Math.max(0, low - spread * .22) : low - spread * .22;
  high += spread * .22;
  const y = value => 185 - (value - low) / (high - low) * 155;
  const points = path.map((p, index) => [60 + index / (path.length - 1) * 520, y(p.balance)]);
  const defs = el('defs'); const gradient = el('linearGradient', {id:'forecast-fill',x1:'0',y1:'0',x2:'0',y2:'1'});
  gradient.append(el('stop', {offset:'0%','stop-color':'#9db8dc','stop-opacity':'.18'}), el('stop', {offset:'100%','stop-color':'#9db8dc','stop-opacity':'.01'}));
  defs.append(gradient); svg.append(defs);
  for (const fraction of [0, .5, 1]) {
    const value = low + (high-low) * fraction, yy = y(value);
    svg.append(el('line',{x1:60,x2:580,y1:yy,y2:yy,stroke:'#2a3c54','stroke-dasharray':'3 6'}));
    const short = Math.abs(value) >= 1000 ? `₹${(value/1000).toFixed(1)}k` : fmt(value);
    svg.append(el('text',{x:48,y:yy+4,'text-anchor':'end',fill:'#94a9c4','font-size':10,'font-family':'system-ui'},short));
  }
  const coords = points.map(p=>p.join(',')).join(' ');
  svg.append(el('polygon',{points:`60,200 ${coords} 580,200`,fill:'url(#forecast-fill)'}));
  const color = path.at(-1).balance < 0 ? '#f2a1a8' : '#b7cce8';
  svg.append(el('polyline',{points:coords,fill:'none',stroke:color,'stroke-width':3,'stroke-linecap':'round','stroke-linejoin':'round'}));
  for (const point of [points[0], points.at(-1)]) svg.append(el('circle',{cx:point[0],cy:point[1],r:4,fill:'#122035',stroke:color,'stroke-width':2}));
  svg.append(el('title',{},`Estimated balance: ${fmt(path[0].balance)} today to ${fmt(path.at(-1).balance)} in 30 days.`));
}

let transactions = [];
let chatHistory = [];
let initialLoad = true;
function showTab(key, focus = false) {
  const valid = ['overview', 'transactions', 'plan', 'ask', 'briefs', 'subscriptions', 'goals', 'settings'];
  if (!valid.includes(key)) key = 'overview';
  for (const name of valid) $(name === 'transactions' ? 'transactions-panel' : name).hidden = name !== key;
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const selected = tab.dataset.tab === key;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected || (key === 'settings' && tab.dataset.tab === 'overview') ? 0 : -1;
  }
  $('current-section').textContent = key === 'settings' ? 'Settings' : $('tab-' + key).textContent;
  if (focus) $(key === 'settings' ? 'settings' : 'tab-' + key).focus();
}
function navigate(key) {
  if (location.hash === '#' + key) showTab(key);
  else location.hash = key;
}
document.querySelectorAll('[data-tab], [data-go]').forEach(button => {
  button.addEventListener('click', () => navigate(button.dataset.tab || button.dataset.go));
});
document.querySelector('[role="tablist"]').addEventListener('keydown', event => {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const current = tabs.indexOf(document.activeElement);
  if (current < 0 || !['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
    (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  navigate(tabs[next].dataset.tab);
  tabs[next].focus();
});
window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
showTab(location.hash.slice(1));
function renderTransactions() {
  const query = $('transaction-search').value.trim().toLowerCase();
  const matches = transactions.filter(t => `${t.merchant} ${t.category.replaceAll('_', ' ')}`.toLowerCase().includes(query));
  $('transactions').replaceChildren();
  for (const t of matches) {
    const tr = document.createElement('tr');
    [t.date, t.merchant, t.category.replaceAll('_', ' '), fmt(t.amount)].forEach(value => tr.append(text('td', value)));
    $('transactions').append(tr);
  }
  if (!matches.length) {
    const tr = document.createElement('tr');
    const td = text('td', transactions.length ? 'No matching transactions.' : 'No transactions yet. Import a CSV or add one above.', 'empty');
    td.colSpan = 4; tr.append(td); $('transactions').append(tr);
  }
  $('transaction-count').textContent = `Showing ${matches.length} of ${transactions.length} loaded transactions (latest 500).`;
}
$('transaction-search').addEventListener('input', renderTransactions);
function recommendation(item) {
  const card = text('div', '', item.trigger === 'negative_forecast' ? 'insight alert' : 'insight');
  card.append(text('p', item.action));
  const details = document.createElement('details');
  details.append(text('summary', 'Why this recommendation?'));
  const evidence = item.evidence;
  if (item.trigger === 'spending_increase') {
    details.append(text('p', `${evidence.category.replaceAll('_', ' ')} spending rose ${evidence.increase_pct}%: ${fmt(evidence.previous)} in the preceding 30 days compared with ${fmt(evidence.current)} in the latest 30 days.`));
  } else if (item.trigger === 'unused_subscription') {
    details.append(text('p', `You confirmed ${evidence.merchant} is unused and active; ${fmt(evidence.amount)} per ${evidence.cycle} cycle. Transaction IDs: ${evidence.transaction_ids.join(', ') || 'manually entered'}.`));
  } else if (item.trigger === 'goal_shortfall') {
    details.append(text('p', `Saved ${fmt(evidence.saved)} toward ${fmt(evidence.target)}. Projected ${fmt(evidence.projected)} by ${evidence.deadline}. ${evidence.assumption}`));
  } else {
    details.append(text('p', `The projected ending balance is ${fmt(evidence.projected_balance)}, based on ${evidence.observed_days} days of history.`));
  }
  card.append(details); return card;
}
async function refresh() {
  const [d, tx, audit] = await Promise.all([api('dashboard'), api('transactions'), api('audit')]);
  transactions = tx;
  renderPlans(d);
  await renderDelivery();
  if (initialLoad) { $('getting-started').open = tx.length === 0; initialLoad = false; }
  if ($('simulation').textContent) { $('simulation').className = 'result'; $('simulation').textContent = 'Your financial data was refreshed. Check this purchase again for an updated estimate.'; }
  $('load-status').textContent = `Updated ${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})} · Analysis as of ${d.as_of}`;
  $('setup').hidden = tx.length > 0;
  $('demo').hidden = tx.length > 0;
  $('next-step').textContent = tx.length ? 'Your history is loaded. Check that your bank balance is up to date, then review your overview or check a purchase.' : 'Next: add your transactions in the Transactions tab, or choose Try sample data above.';
  $('balance').classList.toggle('danger', d.balance < 0);
  $('projected').classList.toggle('danger', d.forecast.available && d.forecast.projected_balance < 0);
  $('balance').textContent = fmt(d.balance);
  $('current-balance').value = d.balance;
  $('projected').textContent = d.forecast.available ? fmt(d.forecast.projected_balance) : 'Add history';
  const expenses = Object.entries(d.analysis.recent_expenses).sort((a,b) => b[1]-a[1]);
  const total = expenses.reduce((sum, entry) => sum + entry[1], 0);
  $('spend').textContent = fmt(total);
  $('ring-total').textContent = fmt(total);
  $('spending-summary').hidden = !expenses.length;
  const categoryColors = {groceries:'#79a4ce', food_delivery:'#73aaa6', transport:'#a396bf', housing:'#8eabc1', subscriptions:'#b4aa92', shopping:'#939fbb', other:'#91a5b7'};
  const categoryColor = name => categoryColors[name] || '#91a5b7';
  let offset = 0;
  const slices = expenses.map((entry, index) => {
    const start = offset; offset += total ? entry[1] / total * 100 : 0;
    return `${categoryColor(entry[0])} ${start}% ${offset}%`;
  });
  $('spending-ring').style.background = slices.length ? `conic-gradient(${slices.join(',')})` : '#2d425e';
  $('spending-ring').setAttribute('role','img');
  $('spending-ring').setAttribute('aria-label', `Total spending in the past 30 days: ${fmt(total)}. Category amounts are listed below.`);
  $('forecast-change').replaceChildren();
  if (d.forecast.available) {
    const change = d.forecast.projected_balance - d.balance;
    $('forecast-change').append(text('strong', `${change >= 0 ? '+' : '−'}${fmt(Math.abs(change))}`), text('span', 'estimated change over 30 days'));
  }

  $('forecast-note').textContent = d.forecast.available ?
    `Based on ${d.forecast.observed_days} days of history. Net cash flow: ${fmt(d.forecast.daily_net)} / day.${d.forecast.limited_history ? ' Less than 30 days of history; this estimate has limited support.' : ''}` : d.forecast.reason;
  draw(d.forecast.path);
  $('categories').replaceChildren();
  if (!expenses.length) $('categories').append(text('p', 'No spending recorded in the past 30 days.', 'empty'));
  for (const [index, [name, amount]] of expenses.entries()) {
    const row = text('div', '', 'category-row');
    row.style.setProperty('--category-color', categoryColor(name));
    const line = text('div', '', 'category-line');
    line.append(text('span', name.replaceAll('_',' ')), text('strong', fmt(amount)));
    const bar = text('div', '', 'bar'), fill = document.createElement('span');
    fill.style.width = `${total ? amount / total * 100 : 0}%`; fill.style.background = categoryColor(name); bar.append(fill);
    row.append(line, bar); $('categories').append(row);
  }
  $('insights').replaceChildren();
  if (!d.recommendations.length) $('insights').append(text('p', tx.length ?
    'No spending increases of 20% or more, or negative ending balance, detected in the available history.' : 'Import transactions to see recommendations.', 'muted'));
  d.recommendations.forEach(item => $('insights').append(recommendation(item)));
  renderTransactions();
  $('audit').replaceChildren();
  if (!audit.length) $('audit').append(text('p', 'No saved briefs yet. Save your first snapshot above.', 'empty'));
  for (const item of audit) {
    const report = item.report, details = text('details', '', 'brief');
    details.append(text('summary', new Date(item.created_at).toLocaleString()));
    details.append(text('span', report.answer ? 'AI conversation' : 'Financial brief', 'badge'));
    details.append(text('p', `Balance: ${fmt(report.balance)} · Forecast: ${report.forecast?.available ? fmt(report.forecast.projected_balance) : 'Unavailable'}`));
    if (report.digest) { details.append(text('p', 'Insight: '+report.digest.insight), text('p', 'Action: '+report.digest.action)); }
    if (report.question) details.append(text('p', `Question: ${report.question}`));
    if (report.answer) details.append(text('p', report.answer));
    const recommendations = report.recommendations || [];
    recommendations.forEach(r => details.append(recommendation(r)));
    if (!recommendations.length) details.append(text('p', 'No recommendation triggers in this snapshot.', 'muted'));
    $('audit').append(details);
  }
}
function on(id,event,action){$(id).addEventListener(event,async e=>{e.preventDefault();const button=e.currentTarget.tagName==='BUTTON'?e.currentTarget:e.currentTarget.querySelector('button');if(button)button.disabled=true;try{await action();}catch(error){notify(error.message,true)}finally{if(button)button.disabled=false}})}
on('balance-form','submit',async()=>{await api('balance',{balance:Number($('current-balance').value)});await refresh();notify('Current balance updated.')});
on('upload','submit',async()=>{const form=new FormData();form.append('file',$('csv').files[0]);const result=await api('import',form);await refresh();notify(`${result.imported} transactions imported; ${result.duplicates_skipped} duplicates skipped.`)});
on('manual','submit',async()=>{await api('transactions',{date:$('tx-date').value,merchant:$('merchant').value,amount:Number($('amount').value)});await refresh();notify('Transaction saved. Update your current balance separately if needed.')});
on('scenario','submit',async()=>{const result=await api('simulate',{cost:Number($('cost').value),days:Number($('days').value),reserve:Number($('reserve').value),monthly_saving:Number($('saving').value)});$('simulation').className=result.affordable_under_model?'result':'result alert';$('simulation').textContent=`${result.affordable_under_model?'This purchase keeps you above your minimum balance in this estimate.':'Alert: this purchase would leave less than your minimum balance.'}\nEstimated balance before purchase: ${fmt(result.baseline)}\nAfter purchase: ${fmt(result.after_purchase)}\nMinimum balance to keep: ${fmt(result.reserve)}\n\n${result.assumptions}`});
on('chat-form','submit',async()=>{$('answer').classList.remove('alert');$('answer').textContent='FinPilot is reviewing your transactions…';try{const result=await api('chat',{question:$('question').value,history:chatHistory.slice(-12)});$('answer').textContent=result.answer;chatHistory.push({role:'user',content:$('question').value},{role:'assistant',content:result.answer.slice(0,6000)})}catch(error){$('answer').classList.add('alert');$('answer').textContent=error.message;throw error}});
on('digest','click',async()=>{await api('digest',{});await refresh();notify('Brief saved with its supporting evidence.')});
on('demo','click',async()=>{await api('demo',{});await refresh();notify('Synthetic demo data loaded. Balance set to ₹45,000.')});
on('connect','click',async()=>{chatHistory=[];token=$('token').value;sessionStorage.setItem('finpilot-token',token);await refresh();notify('Connected.')});
$('tx-date').value=new Date().toLocaleDateString('en-CA');$('tx-date').max=$('tx-date').value;fetch('/health').then(r=>r.json()).then(h=>$('ai-status').textContent=h.mesh_configured?'AI chat configured':'AI chat needs setup').catch(()=>{$('ai-status').textContent='AI status unavailable'});refresh().catch(e=>{$('load-status').textContent='Could not load your data. Check the connection settings and try again.';notify(e.message,true)});

document.querySelectorAll('[data-question]').forEach(button => {
  button.addEventListener('click', () => {
    $('question').value = button.dataset.question;
    $('question').focus();
  });
});
// An edited purchase needs a fresh calculation; do not leave an old result on screen.
$('scenario').addEventListener('input', () => {
  if ($('simulation').textContent) {
    $('simulation').className = 'result';
    $('simulation').textContent = 'Your inputs changed. Choose Check my purchase to update this estimate.';
  }
});

$('today-label').textContent = new Date().toLocaleDateString('en-IN', {day:'numeric',month:'short',year:'numeric'});


function renderPlans(d) {
  $('subscription-list').replaceChildren();
  if (!d.subscriptions.length) $('subscription-list').append(text('p','No subscriptions found. Add a service using the form.'));
  for (const sub of d.subscriptions) {
    const box=text('div','','insight');
    box.append(text('h3',sub.merchant),text('p',`${fmt(sub.amount)} · ${sub.cycle} · Usage: ${sub.usage} · ${sub.confirmed ? (sub.active ? 'Active, confirmed' : 'Inactive, confirmed') : 'Candidate: review billing and usage'}`));
    const edit=text('button','Review details','secondary');edit.type='button';
    edit.onclick=()=>{$('sub-merchant').value=sub.merchant;$('sub-amount').value=sub.amount;$('sub-cycle').value=sub.cycle==='unknown'?'monthly':sub.cycle;$('sub-usage').value=sub.usage;$('sub-active').value=String(sub.active!==false);$('sub-merchant').focus();};
    box.append(edit);$('subscription-list').append(box);
  }
  $('goal-list').replaceChildren();
  if (!d.goals.length) $('goal-list').append(text('p','No saved goals yet.'));
  for (const g of d.goals) {
    const box=text('div','','insight');box.append(text('h3',g.name),text('p',g.status.replaceAll('_',' ')),text('p',`${fmt(g.saved)} saved of ${fmt(g.target)} · Due ${g.deadline}`),text('p',g.status==='needs_history'?'Import recent history to project progress.':`Projected: ${fmt(g.projected)} · Shortfall: ${fmt(g.shortfall)} · Funded monthly contribution: ${fmt(g.allocated_monthly)}`));
    const edit=text('button','Update progress','secondary');edit.type='button';
    edit.onclick=()=>{for(const k of ['id','name','target','saved','monthly','deadline']) $('goal-'+k).value=g[k];$('goal-name').focus();};
    const remove=text('button','Delete goal','secondary');remove.type='button';
    remove.onclick=async()=>{if(!confirm('Delete this savings goal?'))return;try{await api('goals/'+g.id,undefined,'DELETE');await refresh();notify('Goal deleted.');}catch(e){notify(e.message,true);}};
    box.append(edit,remove);$('goal-list').append(box);
  }
}
async function renderDelivery() {
  const d=await api('delivery');
  $('send-email').disabled=!(d.enabled&&d.configured);
  $('email-status').textContent=`Daily schedule: ${String(d.hour_utc).padStart(2,'0')}:00 UTC while the server runs. ${d.enabled&&d.configured?'Email enabled.':'Email needs local configuration and opt-in.'} ${d.records.length?'Latest delivery: '+d.records[0].status:''}`;
}
on('subscription-form','submit',async()=>{
  await api('subscriptions',{merchant:$('sub-merchant').value,amount:Number($('sub-amount').value),cycle:$('sub-cycle').value,usage:$('sub-usage').value,active:$('sub-active').value==='true'},'PUT');
  await refresh();notify('Subscription details saved. No service was cancelled.');
});
on('goal-form','submit',async()=>{
  const id=$('goal-id').value;
  await api('goals'+(id?'/'+id:''),{name:$('goal-name').value,target:Number($('goal-target').value),saved:Number($('goal-saved').value),monthly:Number($('goal-monthly').value),deadline:$('goal-deadline').value},id?'PUT':'POST');
  $('goal-form').reset();$('goal-id').value='';await refresh();notify('Goal saved. No money was moved.');
});
$('new-goal').onclick=()=>{$('goal-form').reset();$('goal-id').value='';};
on('send-email','click',async()=>{if(!confirm('Send today’s financial brief to the email address configured on this server?'))return;await api('digest/send',{});await refresh();notify('Email accepted by the mail server.');});
