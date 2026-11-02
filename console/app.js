/* Hadavin demo console. One export in, four pages out. No network, no storage, text nodes only (no HTML parsing). */
(function () {
  'use strict';

  var root = document.documentElement;
  var main = document.getElementById('main');
  var toast = document.getElementById('toast');
  var hasOwn = Object.prototype.hasOwnProperty;
  function has(o, k) { return o !== null && o !== undefined && hasOwn.call(o, k); }

  // ---- diagnostics read by the render test (no behaviour depends on them)
  root.dataset.jsErrors = '0';
  root.dataset.cspViolations = '0';
  window.addEventListener('error', function () { root.dataset.jsErrors = String(+root.dataset.jsErrors + 1); });
  document.addEventListener('securitypolicyviolation', function () {
    root.dataset.cspViolations = String(+root.dataset.cspViolations + 1);
  });

  // ---- tiny DOM builder: text nodes only; attributes from an allowlist; hrefs are in-page only
  var ATTR = { class: 1, id: 1, role: 1, type: 1, title: 1, tabindex: 1, colspan: 1, scope: 1, for: 1, disabled: 1, value: 1, selected: 1, open: 1, placeholder: 1, name: 1, autocomplete: 1, maxlength: 1, spellcheck: 1, hidden: 1 };
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'href') { if (String(v).charAt(0) === '#') el.setAttribute('href', String(v)); return; }
        if (has(ATTR, k) || k.indexOf('aria-') === 0 || k.indexOf('data-') === 0) el.setAttribute(k, v === true ? '' : String(v));
      });
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, kid) {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(el, k); }); return; }
    el.appendChild(kid && kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  function setKids(el, kids) { el.textContent = ''; add(el, kids); }

  var D;
  try { D = JSON.parse(document.getElementById('export-data').textContent); } catch (e) {
    setKids(main, h('div', { class: 'note note-bad', role: 'alert' }, 'The embedded export could not be read. Nothing is shown.'));
    return;
  }

  // ---- formatting
  function words(s) { return String(s).replace(/_/g, ' '); }
  function short(hash) { return hash ? String(hash).slice(0, 12) + '…' : '—'; }
  function pct(x, digits) { return x === null || x === undefined ? 'n/a' : (x * 100).toFixed(digits === undefined ? 1 : digits) + ' %'; }
  var ZERO_DEC = { JPY: 1, KRW: 1 };
  function money(minor, cur) {
    var m = String(minor);
    if (!/^-?\d+$/.test(m) || !/^[A-Z]{3}$/.test(String(cur))) return m;
    var neg = m.charAt(0) === '-', s = m.replace('-', ''), dec = has(ZERO_DEC, cur) ? 0 : 2;
    while (s.length <= dec) s = '0' + s;
    var whole = s.slice(0, s.length - dec).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + whole + (dec ? '.' + s.slice(-dec) : '') + ' ' + cur;
  }
  function val(v, cur) {
    if (v === null || v === undefined) return '—';
    if (cur) return money(v, cur);
    return typeof v === 'object' ? JSON.stringify(v) : String(v);
  }
  function cmpVal(v, c) {
    if (c.unit === 'minor_units' && c.currency) return money(v, c.currency);
    if (c.unit === 'percent') return v + ' %';
    return v;
  }
  var TONE = {
    approved: 'ok', pending: 'warn', rejected: 'bad', expired: 'neutral',
    proposed: 'info', auto_allowed: 'ok', blocked: 'bad', abstained: 'neutral', failed: 'bad', unknown: 'warn', no_action: 'neutral',
    allow: 'ok', deny: 'bad', needs_approval: 'warn',
    applied: 'ok', duplicate_ignored: 'info', fault_injected: 'bad', read_ok: 'info', read_fault: 'bad', ok: 'ok', error: 'bad',
    pass: 'ok', fail: 'bad', na: 'neutral', auto: 'info', human: 'neutral', four_eyes: 'info', four_eyes_over_max: 'info',
    read: 'neutral', draft: 'info', money: 'warn', approve: 'ok', reject: 'bad',
    green: 'ok', amber: 'warn', not_demonstrated: 'neutral', aborted: 'neutral', reconciled: 'ok', manual: 'warn',
    held: 'warn', conflict: 'bad', human_nonmoney: 'neutral', controller_override: 'info', processed: 'ok', dead_lettered: 'bad', rejected: 'bad'
  };
  function chip(text, tone) { return h('span', { class: 'chip chip-' + (tone || (has(TONE, text) ? TONE[text] : 'neutral')) }, words(text)); }
  function code(text, full) { return h('code', { class: 'mono', title: full || null }, text); }
  function link(text, href) { return h('a', { href: href, class: 'link' }, text); }

  // ---- lookups (null-prototype maps: a case, action or key named "constructor" is just data)
  var casesById = Object.create(null);
  D.cases.forEach(function (c) { casesById[c.case_id] = c; });
  function norm(s) { return String(s).toLowerCase().replace(/[^a-z0-9]/g, ''); }
  var MONEY = Object.create(null);
  D.registry.tools.forEach(function (t) { if (t.risk === 'money') MONEY[norm(t.name)] = 1; });
  function moneyKey(v, depth) {
    if (depth > 20) return true;
    if (Array.isArray(v)) return v.some(function (x) { return moneyKey(x, depth + 1); });
    if (v && typeof v === 'object') return Object.keys(v).some(function (k) { return moneyName(k) || moneyKey(v[k], depth + 1); });
    return false;
  }
  function isMoney(a) { return MONEY[norm(a.action)] === 1 || moneyKey(a.args, 0); }
  function callsFor(key) { return D.adapter_calls.filter(function (c) { return c.idempotency_key === key; }); }
  function approvalsOf(caseId) { return D.approvals.filter(function (a) { return a.case_id === caseId; }); }
  function caseLink(id) { return link(id, '#/run?case=' + encodeURIComponent(id)); }
  var SCRIPTED = D.run.mode === 'scripted' || D.run.model === 'scripted' || D.scorecard.scripted === true;
  var FIXTURE = !(D.honesty && D.honesty.kind === 'run');
  var HONEST = (function () {
    var ids = [], lp = D.load_profile, pt = D.provider_terms, sg = D.segregation, lg = D.ledger;
    if (FIXTURE) ids.push('fixture');
    if (SCRIPTED) { ids.push('scripted'); if (D.scorecard.verdict === 'green') ids.push('capped_verdict'); }
    if (lp) ids.push(lp.label === 'measured' ? 'load_measured' : 'load_assumed');
    if (pt && pt.no_training_setting === null) ids.push('terms_unrecorded');
    if (sg) { if (!sg.canary.ran) ids.push('canary_not_run'); else if (sg.canary.foreign_hits) ids.push('canary_breach'); }
    if (lg && (lg.non_terminal || lg.missing || lg.duplicated_effects || lg.reconciliation_gap)) ids.push('ledger_breach');
    return ids;
  })();
  var SCRIPTED_TEXT = 'scripted; gate vs gold agreement; not model accuracy';
  function cap(sc) { return SCRIPTED && sc.verdict === 'green' ? 'amber' : sc.verdict; }
  function blockedRow(a) { return a.tier === 'blocked' || !a.eligibility || a.eligibility.ok === false; }
  function tierChip(a) { return a.tier === 'auto' && isMoney(a) ? chip('invalid: auto on money', 'bad') : chip(a.tier); }
  function stepVal(s, v) { return val(v, s.currency); }

  // ---- shared bits
  var toastTimer = null;
  function say(msg) {
    var b = h('button', { type: 'button', class: 'toast-x', 'aria-label': 'Dismiss message' }, 'Dismiss');
    b.addEventListener('click', function () { setKids(toast, null); });
    setKids(toast, [h('span', null, msg), ' ', b]);
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { setKids(toast, null); }, 9000);
  }
  function td() { var a = Array.prototype.slice.call(arguments); return h.apply(null, ['td', null].concat(a)); }
  function table(head, rows, label) {
    var t = h('table', { class: 'tbl' }, h('thead', null, h('tr', null, head.map(function (c) { return h('th', { scope: 'col' }, c); }))), h('tbody', null, rows));
    return h('div', { class: 'scroll', role: 'region', tabindex: 0, 'aria-label': label }, t);
  }
  function kv(obj) {
    var keys = Object.keys(obj || {});
    if (!keys.length) return h('p', { class: 'muted' }, 'none');
    return h('dl', { class: 'kv' }, keys.map(function (k) {
      var cur = typeof obj.currency === 'string' ? obj.currency : null;
      return [h('dt', null, words(k)), h('dd', null, /minor$/.test(k) && cur ? money(obj[k], cur) : val(obj[k]))];
    }));
  }
  function stat(label, value, sub, tone) {
    return h('div', { class: 'stat' + (tone ? ' stat-' + tone : '') }, h('div', { class: 'stat-label' }, label), h('div', { class: 'stat-value' }, value), sub ? h('div', { class: 'stat-sub' }, sub) : null);
  }
  function pair(st, def) { return h('div', { class: 'statdef' }, st, h('p', { class: 'def' }, def)); }
  function section(title, kids, id) { return h('section', { class: 'panel', 'aria-labelledby': id }, h('h2', { id: id }, title), kids); }
  function pageHead(title, sub) { return h('header', { class: 'page-head' }, h('h1', { id: 'page-title', tabindex: -1 }, title), h('p', { class: 'muted' }, sub)); }
  function comparisonTable(list, label) {
    return table(['Field', 'Source system', 'Model', 'Check'], list.map(function (c) {
      return h('tr', null, td(c.field), td(cmpVal(c.source, c)), td(cmpVal(c.model, c)), td(c.source === c.model ? chip('match', 'ok') : chip('differs', 'bad')));
    }), label);
  }

  // ---- audit-chain state, recomputed here on load: shown on every page when it is not clean
  var CHAIN = verifyChain(D.audit.events, D.audit.chain);
  var CLAIM = D.audit.verify;
  var CHAIN_AGREES = CHAIN.matched === CLAIM.matched && CHAIN.events === CLAIM.events && CHAIN.ok === CLAIM.ok;
  var CHAIN_OK = CHAIN.ok && CHAIN_AGREES;
  var HEAD = D.audit.events.length ? D.audit.events[D.audit.events.length - 1].sha256 : GENESIS;
  function chainLine() {
    return 'Export claims ' + CLAIM.matched + ' of ' + CLAIM.events + ' events match; recomputed in this page: ' + CHAIN.matched + ' of ' + CHAIN.events + '.';
  }

  // ---- page: approvals (home)
  var state = { filter: 'all', q: '', limit: 10 };
  function otherCase(c) {
    return h('article', { class: 'card', 'aria-label': 'Case ' + c.case_id },
      h('div', { class: 'card-head' }, h('h2', null, words(c.outcome) + ': ' + c.case_id), h('div', { class: 'chips' }, chip(c.outcome), caseLink(c.case_id))),
      h('p', null, c.input_summary),
      c.hard_zero_flags.length ? h('p', null, c.hard_zero_flags.map(function (f) { return chip('hard zero: ' + f, 'bad'); })) : null,
      c.reconcile ? reconcileNote(c) : (c.outcome === 'unknown' ? h('p', { class: 'note note-warn' }, 'Unknown outcome. No reconcile step is recorded in this export.') : null),
      h('p', { class: 'muted' }, 'Sample data.'));
  }
  function reconcileNote(c) {
    return c.reconcile ? h('p', { class: 'note note-' + (c.reconcile.state === 'reconciled' ? 'ok' : 'warn') }, 'Reconcile: ', chip(c.reconcile.state), ' ', c.reconcile.note, c.reconcile.call_id ? [' Replay call ', code(c.reconcile.call_id)] : null) : null;
  }
  function deniedCard(c) {
    var deny = c.steps.filter(function (s) { return s.decision === 'deny'; })[0];
    var proposed = c.steps.filter(function (s) { return s.model_value !== null; })[0];
    var rows = approvalsOf(c.case_id);
    return h('article', { class: 'card card-bad', 'aria-label': 'Denied: ' + c.case_id },
      h('div', { class: 'card-head' }, h('h2', null, 'Denied by the gate'), h('div', { class: 'chips' }, chip('blocked'), deny && deny.rule_id ? code(deny.rule_id) : null, caseLink(c.case_id))),
      h('p', null, c.input_summary),
      deny ? h('p', null, h('strong', null, 'Why: '), deny.reason) : null,
      proposed ? h('p', { class: 'muted' }, 'The model proposed ' + stepVal(proposed, proposed.model_value) + (proposed.source_value !== null ? '; the system of record says ' + stepVal(proposed, proposed.source_value) : '') + '.') : null,
      h('p', { class: 'muted' }, rows.length ? rows.length + ' approval row(s) recorded for this case; they are shown in this queue and cannot release anything.' : 'No approval was created and no adapter call was made.'),
      h('p', { class: 'muted' }, 'Sample data.'));
  }
  function approvalCard(a) {
    var c = has(casesById, a.case_id) ? casesById[a.case_id] : null;
    var calls = callsFor(a.idempotency_key), ex = a.executed;
    var cmp = a.comparisons || (c ? c.steps.reduce(function (acc, s) { return acc.concat(s.comparisons || []); }, []) : []);
    var applied = calls.filter(function (x) { return x.outcome === 'applied'; }).length;
    var dup = calls.filter(function (x) { return x.outcome === 'duplicate_ignored'; }).length;
    var execBlock;
    if (!ex) {
      execBlock = h('p', { class: 'muted' }, a.status === 'pending' ? 'Not executed: waiting for approval.' : 'Not executed: no adapter call was made.');
    } else {
      execBlock = h('div', null,
        h('p', null, 'Port ', code(ex.port), ' call ', code(ex.call_id), ' ', chip(ex.result), ' ', h('span', { class: 'muted' }, ex.ts)),
        ex.error_code ? h('p', { class: 'note note-bad' }, 'Error ', code(ex.error_code), ' ', ex.error_message || '') : null,
        ex.result === 'unknown' ? (c && c.reconcile ? reconcileNote(c) : h('p', { class: 'note note-warn' }, 'Unknown outcome: the call may or may not have been applied. No reconcile step is recorded in this export. In this fake adapter a replay of the same idempotency key is ignored as a duplicate; real systems differ.')) : null,
        calls.length > 1 ? h('p', { class: 'muted' }, 'Same idempotency key sent ' + calls.length + ' times: ' + applied + ' applied, ' + dup + ' duplicate ignored (this fake adapter; real systems differ).') : null);
    }
    var decided = a.approvers.map(function (p) { return p.role; });
    var awaiting = (a.required_roles || []).filter(function (r) { return decided.indexOf(r) < 0; });
    var blocked = blockedRow(a);
    var buttons = a.status === 'pending' && !blocked ? h('div', { class: 'btn-row' },
      h('button', { type: 'button', class: 'btn btn-primary', id: 'btn-approve-' + a.approval_id, 'data-act': 'approve' }, 'Approve (sample only)'),
      h('button', { type: 'button', class: 'btn', id: 'btn-reject-' + a.approval_id, 'data-act': 'reject' }, 'Reject (sample only)')) : null;
    var amount = has(a.args, 'amount_minor') && typeof a.args.currency === 'string' ? money(a.args.amount_minor, a.args.currency) : '';
    var body = [
      blocked ? h('p', { class: 'note note-bad' }, h('strong', null, 'Blocked, cannot be approved. '), a.eligibility && a.eligibility.ok === false ? a.eligibility.reason : 'This row is blocked by its rule.') : null,
      h('p', { class: 'muted' }, 'Requested ' + a.requested_at + (a.requested_by ? ' by ' + a.requested_by : '') + (a.expires_at ? '; expires ' + a.expires_at : '') + '.'),
      h('div', { class: 'grid2' },
        h('div', null, h('h3', null, 'Evidence'), h('dl', { class: 'kv' }, a.evidence.map(function (e) { return [h('dt', null, e.label), h('dd', null, e.value)]; }))),
        h('div', null, h('h3', null, 'Source system vs model'), cmp.length ? comparisonTable(cmp, 'Source versus model values') : h('p', { class: 'muted' }, 'No structured comparison recorded for this approval.'))),
      h('details', { class: 'more' }, h('summary', null, 'Arguments sent to the port'), kv(a.args), h('p', { class: 'muted' }, 'Idempotency key ', code(short(a.idempotency_key), a.idempotency_key))),
      a.lifecycle || a.plan_step || (a.excluded_user_ids && a.excluded_user_ids.length) || a.bound ? h('dl', { class: 'kv' },
        a.lifecycle ? [h('dt', null, 'Demo state'), h('dd', null, code(a.lifecycle))] : null,
        a.plan_step ? [h('dt', null, 'Plan step'), h('dd', null, code(a.plan_step))] : null,
        a.excluded_user_ids && a.excluded_user_ids.length ? [h('dt', null, 'Barred from deciding'), h('dd', null, a.excluded_user_ids.map(function (u) { return [code(u), ' ']; }))] : null,
        a.bound ? [h('dt', null, 'Decision bound to'), h('dd', null, 'plan ', code(short(a.bound.plan_sha256), a.bound.plan_sha256), ' snapshot ', code(short(a.bound.snapshot_sha256), a.bound.snapshot_sha256))] : null) : null,
      h('h3', null, 'Decision'),
      a.approvers.length ? h('ol', { class: 'plain ordered' }, a.approvers.map(function (p, i) {
        return h('li', null, (i + 1) + '. ', code(p.role), p.person_ref ? [' ', code(p.person_ref)] : null, ' ', chip(p.decision), ' ', h('span', { class: 'muted' }, p.ts), ' ', p.sig_ok ? h('span', { class: 'muted' }, 'signature ok' + (p.sig_scheme === 'hmac_shared_secret' ? ' (shared-secret HMAC: a verifier holding the key could forge it)' : p.sig_scheme ? ' (scheme label: ' + p.sig_scheme + '; the export carries no key or signed payload, so this page cannot verify it)' : '')) : chip('signature not verified', 'bad'),
          p.roles_at_decision ? h('span', { class: 'muted' }, ' roles at decision: ' + p.roles_at_decision.join(', ')) : null);
      })) : h('p', { class: 'muted' }, 'No decision yet.'),
      awaiting.length ? h('p', { class: 'muted' }, 'Still awaited: ' + awaiting.join(', ') + '.') : null,
      a.eligibility && a.eligibility.ok === false ? h('p', { class: 'note note-bad' }, h('strong', null, 'Not eligible: '), a.eligibility.reason) : null,
      h('h3', null, 'Execution'), execBlock, buttons,
      h('p', { class: 'muted print-note' }, 'prototype, sample data')
    ];
    var head = h('div', { class: 'chips' }, chip(a.status), tierChip(a), code(a.rule_id), caseLink(a.case_id));
    if (a.status === 'approved' && !blocked && !(ex && ex.result === 'unknown')) {
      return h('details', { class: 'card card-fold', id: 'card-' + a.approval_id },
        h('summary', null, h('span', { class: 'sum-title' }, words(a.action) + (amount ? ' ' + amount : '')), ' ', head), body);
    }
    return h('article', { class: 'card', 'aria-labelledby': 'ap-' + a.approval_id, id: 'card-' + a.approval_id },
      h('div', { class: 'card-head' }, h('h2', { id: 'ap-' + a.approval_id }, words(a.action) + (amount ? ' ' + amount : '')), head), body);
  }
  function pageApprovals(params) {
    var A = D.approvals, cases = D.cases;
    var n = function (st) { return A.filter(function (a) { return a.status === st; }).length; };
    var unknown = cases.filter(function (c) { return c.outcome === 'unknown'; }).length;
    var denied = cases.filter(function (c) { return c.outcome === 'blocked'; });
    var other = cases.filter(function (c) { return ['failed', 'abstained', 'no_action', 'unknown', 'held'].indexOf(c.outcome) >= 0; });
    var flagged = cases.filter(function (c) { return c.hard_zero_flags.length > 0; });
    var list = h('div', { class: 'stack', id: 'queue' });
    function items() {
      var q = state.q.trim().toLowerCase();
      var match = function (text) { return !q || text.toLowerCase().indexOf(q) >= 0; };
      var pend = A.filter(function (a) { return a.status === 'pending'; });
      var rest = A.filter(function (a) { return a.status !== 'pending'; });
      var odd = rest.filter(function (a) { return a.status === 'rejected' || a.status === 'expired' || (a.executed && a.executed.result === 'unknown') || blockedRow(a); });
      var plain = rest.filter(function (a) { return odd.indexOf(a) < 0; });
      var out = [];
      var ap = function (a) { return { key: a.case_id + ' ' + a.action + ' ' + a.approval_id + ' ' + a.rule_id, node: function () { return approvalCard(a); } }; };
      var dn = function (c) { return { key: c.case_id + ' ' + c.input_summary, node: function () { return deniedCard(c); } }; };
      var ot = function (c) { return { key: c.case_id + ' ' + c.input_summary + ' ' + c.outcome, node: function () { return otherCase(c); } }; };
      var f = state.filter;
      if (f === 'all') out = pend.map(ap).concat(odd.map(ap), denied.map(dn), other.map(ot), plain.map(ap));
      if (f === 'pending') out = pend.map(ap);
      if (f === 'decided') out = rest.map(ap);
      if (f === 'stopped') out = odd.filter(function (a) { return a.status === 'rejected' || blockedRow(a); }).map(ap).concat(denied.map(dn));
      if (f === 'other') out = other.map(ot);
      return out.filter(function (x) { return match(x.key); });
    }
    function paint() {
      var all = items(), shown = all.slice(0, state.limit);
      var kids = shown.map(function (x) { return x.node(); });
      if (!all.length) kids.push(h('p', { class: 'muted empty' }, D.cases.length ? 'Nothing matches in ' + D.demo.title + '.' : 'This export has no cases for ' + D.demo.title + '.'));
      if (all.length > shown.length) {
        var more = h('button', { type: 'button', class: 'btn', id: 'show-more' }, 'Show more (' + (all.length - shown.length) + ' left)');
        more.addEventListener('click', function () { state.limit += 10; paint(); var m = document.getElementById('show-more'); if (m) m.focus(); });
        kids.push(more);
      }
      setKids(list, kids);
      list.querySelectorAll('button[data-act]').forEach(function (b) {
        b.addEventListener('click', function () { say('Sample only: the ' + b.getAttribute('data-act') + ' button writes nothing. No decision was recorded and nothing was sent to a port.'); });
      });
    }
    var bar = h('div', { class: 'filters', role: 'group', 'aria-label': 'Filter the queue' });
    [['all', 'All'], ['pending', 'Pending'], ['decided', 'Decided'], ['stopped', 'Rejected and denied'], ['other', 'Other outcomes']].forEach(function (f) {
      var b = h('button', { type: 'button', class: 'pill', id: 'f-' + f[0], 'aria-pressed': state.filter === f[0] ? 'true' : 'false' }, f[1]);
      b.addEventListener('click', function () {
        state.filter = f[0]; state.limit = 10;
        bar.querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', 'false'); });
        b.setAttribute('aria-pressed', 'true');
        paint();
      });
      bar.appendChild(b);
    });
    var search = h('input', { type: 'text', id: 'q', class: 'input', placeholder: 'Search case, action or rule', autocomplete: 'off', maxlength: 80, 'aria-label': 'Search the queue', value: state.q });
    search.addEventListener('input', function () { state.q = search.value; state.limit = 10; paint(); });
    paint();
    var hon = D.honesty;
    var faults = D.adapter_calls.some(function (c) { return c.outcome === 'fault_injected'; });
    return [
      pageHead('Approvals', 'Actions the agent proposed, what the gate decided, and who approved. Pending and unusual items come first; denied, rejected and other outcomes are listed, not hidden.'),
      flagged.length ? h('div', { class: 'note note-bad', role: 'alert' }, h('strong', null, 'Hard zero flagged on ' + flagged.length + ' case(s): '), flagged.map(function (c) { return [caseLink(c.case_id), ' (' + c.hard_zero_flags.join(', ') + ') ']; })) : null,
      h('aside', { class: 'honesty', 'aria-label': 'What is real here' }, h('strong', null, 'What is real here. '),
        'Synthetic data only. Model: ' + D.run.model + ' (' + D.run.mode + ' run, level ' + D.run.level + '). Adapters: ' + hon.adapters + (faults ? ', with injected faults' : '') + '. ' + hon.notes.join(' ')),
      h('div', { class: 'stats' },
        stat('Pending', String(n('pending')), 'waiting for a person', n('pending') ? 'warn' : null),
        stat('Approved', String(n('approved')), 'decided by a person'),
        stat('Rejected', String(n('rejected')), 'a person said no', n('rejected') ? 'bad' : null),
        stat('Denied by gate', String(denied.length), 'stopped by plain code', denied.length ? 'bad' : null),
        stat('Unknown outcome', String(unknown), 'needs reconciling', unknown ? 'warn' : null)),
      h('div', { class: 'tools' }, bar, h('label', { for: 'q', class: 'label' }, 'Search'), search),
      list
    ];
  }

  // ---- page: run and audit
  var showAll = false;
  function pageRun(params) {
    var id = has(params, 'case') && has(casesById, params.case) ? params.case : (D.cases[0] || {}).case_id;
    if (!id) return [pageHead('Run and audit', 'No cases in this export for ' + D.demo.title + '.')];
    var c = casesById[id];
    var sel = h('select', { id: 'case-pick', class: 'select' }, D.cases.map(function (x) {
      return h('option', { value: x.case_id, selected: x.case_id === id }, x.case_id + ' · ' + x.bucket + ' · ' + words(x.outcome));
    }));
    sel.addEventListener('change', function () { location.hash = '#/run?case=' + encodeURIComponent(sel.value) + themeParam(); });
    var A = approvalsOf(id), a = A[0] || null, calls = [];
    A.forEach(function (x) { calls = calls.concat(callsFor(x.idempotency_key)); });
    var reads = D.adapter_calls.filter(function (x) { return x.outcome === 'read_ok' || x.outcome === 'read_fault'; });
    var cnt = function (o) { return calls.filter(function (x) { return x.outcome === o; }).length; };
    var evSet = Object.create(null);
    c.audit_event_ids.forEach(function (e) { evSet[e] = 1; });
    var verifyOut = h('div', { id: 'verify-out', 'aria-live': 'polite' });
    var COVERS = 'Covers the audit event text fields only (type, actor, case, summary, time and order), not approvals, amounts or the scorecard. Recomputed with SHA-256 in this page. Tamper-evident, not tamper-proof.';
    function report(label, r, extra, bad) {
      return h('div', { class: 'note note-' + (r.ok && !bad ? 'ok' : 'bad') }, h('strong', null, label), ' local integrity check: ' + r.matched + ' of ' + r.events + ' events match. ',
        r.ok ? chip('internally consistent', 'ok') : chip('mismatch at event ' + r.firstBad, 'bad'), ' ', extra || '', h('div', { class: 'muted' }, COVERS));
    }
    var vbtn = h('button', { type: 'button', class: 'btn btn-primary', id: 'btn-verify' }, 'Verify chain');
    vbtn.addEventListener('click', function () {
      var r = verifyChain(D.audit.events, D.audit.chain), same = r.matched === CLAIM.matched && r.events === CLAIM.events && r.ok === CLAIM.ok;
      setKids(verifyOut, report('Recomputed now.', r, same ? 'It agrees with the export’s own claim.' : 'It DISAGREES with the export’s own claim. ' + chainLine(), !same));
    });
    var tbtn = h('button', { type: 'button', class: 'btn', id: 'btn-tamper' }, 'Tamper test: how the check reacts to an edit');
    tbtn.addEventListener('click', function () {
      var copy = D.audit.events.map(function (e) { return Object.assign({}, e); });
      if (!copy.length) return;
      var k = Math.floor(copy.length / 2);
      copy[k].summary = copy[k].summary + ' (edited)';
      setKids(verifyOut, report('Edited event ' + copy[k].seq + ' in a copy.', verifyChain(copy, D.audit.chain), 'This only shows how the check reacts; it is not a check of this file. The export is untouched.', true));
    });
    var paste = h('input', { type: 'text', id: 'head-paste', class: 'input', autocomplete: 'off', spellcheck: 'false', maxlength: 64, placeholder: 'head hash recorded elsewhere (64 hex)', 'aria-label': 'Head hash recorded elsewhere' });
    var pbtn = h('button', { type: 'button', class: 'btn', id: 'btn-paste' }, 'Compare with head');
    pbtn.addEventListener('click', function () {
      var v = paste.value.trim().toLowerCase();
      setKids(verifyOut, h('div', { class: 'note note-' + (v === HEAD ? 'ok' : 'bad') }, v === HEAD ? 'The pasted hash equals this export’s head hash.' : 'The pasted hash does NOT equal this export’s head hash (' + short(HEAD) + ').'));
    });
    var evs = D.audit.events.filter(function (e) { return showAll || evSet[e.event_id] === 1; });
    var toggle = h('button', { type: 'button', class: 'pill', id: 'show-all', 'aria-pressed': showAll ? 'true' : 'false' }, showAll ? 'Showing all events' : 'Showing this case only');
    toggle.addEventListener('click', function () { showAll = !showAll; render(); });
    var detail = c.steps.filter(function (s) { return s.model_saw || Object.keys(s.confidence || {}).length; });
    var res = [
      pageHead('Run and audit', 'One case at a time: what the agent did step by step, what reached the adapter, and the audit chain behind it.'),
      section('Run', h('dl', { class: 'kv kv-wide' },
        h('dt', null, 'Run'), h('dd', null, code(D.run.run_id), ' ', h('span', { class: 'muted' }, D.run.ts)),
        h('dt', null, 'Model'), h('dd', null, chip(D.run.model, D.run.model === 'scripted' ? 'info' : 'neutral'), ' ', D.run.mode + ' run, level ' + D.run.level + ', seed ' + D.run.seed),
        h('dt', null, 'Level'), h('dd', null, D.registry.level_note || D.run.level),
        D.run.recorded_turns_sha256 ? [h('dt', null, 'Recorded turns replayed'), h('dd', null, code(short(D.run.recorded_turns_sha256), D.run.recorded_turns_sha256))] : null,
        h('dt', null, 'Prompt / config / data'), h('dd', null, code(short(D.run.prompt_sha256), D.run.prompt_sha256), ' ', code(short(D.run.config_hash), D.run.config_hash), ' ', code(short(D.run.dataset_sha256), D.run.dataset_sha256)),
        h('dt', null, 'Build'), h('dd', null, 'v' + D.demo.version + ', core ' + D.demo.core_commit + ', kit ' + D.demo.kit_commit)), 'h-run'),
      section('Case', [
        h('label', { for: 'case-pick', class: 'label' }, 'Case'), sel,
        h('p', { class: 'case-summary' }, c.input_summary),
        h('div', { class: 'chips' }, chip(c.bucket, 'neutral'), chip(c.split, 'neutral'),
          h('span', { class: 'muted' }, 'expected ' + words(c.expected.outcome) + ' → got'), chip(c.outcome),
          c.correct ? chip('correct', 'ok') : chip('not correct', 'bad'),
          c.wrong_confident ? chip('confident and wrong', 'bad') : null,
          c.hard_zero_flags.map(function (f) { return chip('hard zero: ' + f, 'bad'); }),
          c.tier ? [h('span', { class: 'muted' }, 'tier'), chip(c.tier)] : null,
          c.terminal_state ? [h('span', { class: 'muted' }, 'message ended'), chip(c.terminal_state.state), c.terminal_state.reason ? h('span', { class: 'muted' }, c.terminal_state.reason) : null] : null,
          c.queue ? [h('span', { class: 'muted' }, 'queue'), code(c.queue)] : null,
          c.receipt_id ? [h('span', { class: 'muted' }, 'receipt'), code(c.receipt_id)] : null,
          (c.exceptions || []).map(function (x) { return [chip('exception: ' + x.code, 'warn'), code(x.rule_id)]; })),
        c.expected.tier || c.expected.exceptions || c.expected.actions ? h('p', { class: 'muted' }, 'Expected: ' + [c.expected.tier ? 'tier ' + c.expected.tier : '', c.expected.exceptions ? 'exceptions ' + c.expected.exceptions.join(', ') : '', c.expected.actions ? 'actions ' + (c.expected.actions.join(', ') || 'none') : ''].filter(Boolean).join('; ') + '.') : null,
        reconcileNote(c),
        c.source_document_sha256 ? h('p', { class: 'muted' }, 'Source document hash ', code(short(c.source_document_sha256), c.source_document_sha256)) : null,
        c.expected.checks.length ? h('p', { class: 'muted' }, 'Checks: ' + c.expected.checks.join('; ') + '.') : null
      ], 'h-case')
    ];
    if (c.facts && c.facts.length) {
      var groups = Object.create(null), order = [];
      c.facts.forEach(function (f) { if (!groups[f.group]) { groups[f.group] = []; order.push(f.group); } groups[f.group].push(f); });
      res.push(section('Facts', order.map(function (g) {
        return [h('h3', null, words(g)), h('dl', { class: 'kv kv-wide' }, groups[g].map(function (f) { return [h('dt', null, f.label), h('dd', null, f.value)]; }))];
      }), 'h-facts'));
    }
    res.push(section('Steps', [table(['#', 'Kind', 'Step', 'Rule', 'Decision', 'Reason', 'Source / model', 'ms', 'USD'], c.steps.map(function (s) {
      return h('tr', null, td(String(s.n)), td(chip(s.kind, 'neutral')), td(s.name, s.actor ? h('div', { class: 'muted' }, s.actor) : null), td(s.rule_id ? code(s.rule_id) : '—'),
        td(s.decision ? chip(s.decision) : '—'), td(s.reason || '—'),
        td(s.source_value !== null || s.model_value !== null ? 'source ' + stepVal(s, s.source_value) + ' / model ' + stepVal(s, s.model_value) : '—'),
        td(String(s.ms)), td(s.cost_usd));
    }), 'Steps of the case'),
      detail.length ? h('div', { class: 'more' }, detail.map(function (s) {
        return h('details', null, h('summary', null, 'Step ' + s.n + ' detail: ' + s.name),
          s.model_saw ? [h('h3', null, 'What the model saw, after redaction'), h('pre', { class: 'mono pre' }, s.model_saw)] : null,
          Object.keys(s.confidence || {}).length ? [h('h3', null, 'Confidence' + (SCRIPTED ? ' (scripted: plumbing only, not a measure)' : '')), kv(s.confidence)] : null);
      })) : null], 'h-steps'));
    res.push(section('Adapter calls', calls.length ? [
      h('p', null, A.length > 1 ? A.length + ' approval rows, ' : ['Idempotency key ', code(short(a.idempotency_key), a.idempotency_key), ': '], calls.length + ' call' + (calls.length > 1 ? 's' : '') + ', ' + cnt('applied') + ' applied, ' + cnt('duplicate_ignored') + ' duplicate ignored, ' + cnt('fault_injected') + ' fault injected.'),
      table(['Call', 'Port.op', 'Outcome', 'Fault', 'Time'], calls.map(function (x) { return h('tr', null, td(code(x.call_id)), td(x.port + '.' + x.op), td(chip(x.outcome)), td(x.fault || '—'), td(x.ts)); }), 'Adapter calls of the case'),
      a && a.executed && a.executed.result === 'unknown' ? (c.reconcile ? reconcileNote(c) : h('p', { class: 'note note-warn' }, 'Result unknown and no reconcile step is recorded in this export.')) : null
    ] : h('p', { class: 'muted' }, 'No adapter call was made for this case.'), 'h-calls'));
    if (reads.length) res.push(section('Read calls (all cases)', table(['Call', 'Port.op', 'Outcome', 'Fault', 'Time'], reads.map(function (x) { return h('tr', null, td(code(x.call_id)), td(x.port + '.' + x.op), td(chip(x.outcome)), td(x.fault || '—'), td(x.ts)); }), 'Read calls'), 'h-reads'));
    res.push(section('Audit chain', [
      h('p', null, 'Head ', code(HEAD, HEAD), ' · ' + D.audit.events.length + ' events · ' + D.audit.chain),
      h('p', { class: 'note note-warn' }, 'Export chain only: this is the demo-side copy of the audit trail. It shows the copy was not edited after export; it is not the core audit chain, and no tamper-evidence claim about the audit rests on this page.'),
      h('p', { class: CHAIN_OK ? 'muted' : 'note note-bad' }, chainLine()),
      h('div', { class: 'btn-row' }, vbtn, tbtn, toggle),
      h('div', { class: 'btn-row' }, h('label', { for: 'head-paste', class: 'label' }, 'Head recorded elsewhere'), paste, pbtn),
      verifyOut,
      table(['#', 'Time', 'Type', 'Actor', 'Case', 'What', 'Hash'], evs.map(function (e) {
        return h('tr', { class: evSet[e.event_id] === 1 ? 'hit' : null }, td(String(e.seq)), td(e.ts), td(chip(e.type, e.type === 'deny' ? 'bad' : 'neutral')), td(e.actor), td(e.case_id || '—'), td(e.summary), td(code(short(e.sha256), e.sha256)));
      }), 'Audit events')
    ], 'h-audit'));
    return res;
  }

  // ---- page: scorecard
  function band(sc) {
    var th = sc.thresholds, max = 0.5, cw = sc.confident_wrong;
    var pos = function (x) { return Math.min(100, (x / max) * 100); };
    var bar = h('div', { class: 'band', role: 'img', 'aria-label': 'Confident-wrong rate ' + pct(cw.rate) + ', 95 % upper bound ' + pct(cw.upper_bound) + '. Green up to ' + pct(th.green_max_upper, 0) + ', amber up to ' + pct(th.amber_max_upper, 0) + '.' },
      h('span', { class: 'band-g' }), h('span', { class: 'band-a' }), h('span', { class: 'band-r' }),
      cw.rate !== null ? h('span', { class: 'mark mark-rate' }) : null, cw.upper_bound !== null ? h('span', { class: 'mark mark-bound' }) : null);
    bar.style.setProperty('--g', pos(th.green_max_upper) + '%');
    bar.style.setProperty('--a', pos(th.amber_max_upper) + '%');
    var mr = bar.querySelector('.mark-rate'), mb = bar.querySelector('.mark-bound');
    if (mr) mr.style.setProperty('--at', pos(cw.rate) + '%');
    if (mb) mb.style.setProperty('--at', pos(cw.upper_bound) + '%');
    return h('div', null, bar,
      h('div', { class: 'band-scale muted' }, h('span', null, '0 %'), h('span', null, 'green ≤ ' + pct(th.green_max_upper, 0)), h('span', null, 'amber ≤ ' + pct(th.amber_max_upper, 0)), h('span', null, '50 %+')),
      h('ul', { class: 'legend plain' }, h('li', null, h('span', { class: 'sw sw-rate' }), ' point rate (what happened on this set)'), h('li', null, h('span', { class: 'sw sw-bound' }), ' 95 % upper bound (the number the gate uses)'),
        h('li', null, h('span', { class: 'sw sw-g' }), 'green band ', h('span', { class: 'sw sw-a' }), 'amber band ', h('span', { class: 'sw sw-r' }), 'above amber: not demonstrated')),
      SCRIPTED ? h('p', { class: 'note note-warn' }, 'Scripted run: the verdict is capped below green however low the rate is, because a replayed script says nothing about a real model.') : null);
  }
  var VERDICT_LABEL = { green: 'Demonstrated (smoke)', amber: 'Promising, not demonstrated', not_demonstrated: 'Not demonstrated', aborted: 'No verdict: run aborted' };
  function pageScorecard() {
    var sc = D.scorecard, cw = sc.confident_wrong, fa = sc.false_abstain, wa = sc.wrong_and_allowed, v = cap(sc);
    var breached = Object.keys(sc.hard_zeros).filter(function (k) { return sc.hard_zeros[k] > 0; });
    var heading = SCRIPTED ? 'Plumbing check only, no accuracy claim' : (has(VERDICT_LABEL, v) ? VERDICT_LABEL[v] : v);
    return [
      pageHead('Scorecard', 'A smoke check of this agent on synthetic cases. Read the interval, not just the pass rate.'),
      h('section', { class: 'verdict verdict-' + v, 'aria-labelledby': 'verdict-h' },
        h('div', { class: 'chips' }, chip(v), SCRIPTED ? chip('plumbing check, scripted model', 'info') : null, FIXTURE ? chip('fixture', 'warn') : null,
          h('span', { class: 'muted' }, 'smoke, n=' + sc.n + ' (' + sc.n_answered + ' expected to get a definite response (an answer or a refusal)' + (sc.n_refused !== undefined ? ', ' + sc.n_refused + ' to refuse' : '') + '), ' + sc.split + ' split, ' + sc.label)),
        h('h2', { id: 'verdict-h' }, heading),
        SCRIPTED ? h('p', { class: 'note note-warn' }, SCRIPTED_TEXT + '.') : null,
        SCRIPTED ? h('p', null, 'Gate result on the scripted numbers: ' + (v === 'amber' ? 'amber (plumbing only)' : (has(VERDICT_LABEL, v) ? VERDICT_LABEL[v] : v)) + '. This is a test of the plumbing (cases, gate, audit, console), not of a model.') : h('p', null, sc.verdict_text + (breached.length ? '. Hard zero breached: ' + breached.join(', ') : '')),
        SCRIPTED && breached.length ? h('p', null, 'Hard zero breached: ' + breached.join(', ')) : null,
        h('p', { class: 'muted' }, sc.disclaimer)),
      section('Confident-wrong rate', [
        SCRIPTED ? h('p', { class: 'note note-warn' }, SCRIPTED_TEXT + '. The rates and bounds below describe the replayed script, not a model.') : null,
        h('div', { class: 'stats' }, stat('Confident and wrong', cw.x + ' of ' + cw.n_answered, 'items expected to get a definite response', cw.x ? 'bad' : null), stat('Point rate', pct(cw.rate), 'what happened on this set'), stat('95 % upper bound', pct(cw.upper_bound), 'what it could be at worst, one-sided')),
        band(sc),
        h('p', { class: 'muted' }, 'The headline number. A small set gives a wide bound: zero errors in few items still leaves a high ceiling. Items: ' + (cw.items.length ? cw.items.join(', ') : 'none') + '.')
      ], 'h-cw'),
      section('The other safety numbers', [SCRIPTED ? h('p', { class: 'note note-warn' }, SCRIPTED_TEXT + '. Every figure here is a plumbing check, not model accuracy.') : null, h('div', { class: 'stats stats-defs' },
        pair(stat('False abstain', fa.x + ' of ' + fa.n_should_answer, fa.rate === null ? 'n/a' : pct(fa.rate) + ' of items that should have been acted on', fa.x ? 'warn' : null), 'The agent handed back something it should have handled. Safe, but it costs coverage.'),
        sc.false_hold ? pair(stat('False hold', sc.false_hold.x + ' of ' + sc.false_hold.n, 'held although it should have been acted on', sc.false_hold.x ? 'warn' : null), 'Reported beside false abstain, never blended into it.') : null,
        sc.steered ? pair(stat('Steered replays denied', sc.steered.x + ' of ' + sc.steered.n, sc.steered.label), 'Recorded adversarial turns the pre-gate denied. Not part of the pass rate or confident-wrong.') : null,
        pair(stat('Wrong and allowed', wa.x + ' of ' + wa.n, 'wrong actions that got through the gate', wa.x ? 'bad' : null), 'A wrong or unsafe action that the gate allowed and that reached an approver or the adapter. Must be zero. A bad proposal a person rejected does not count here.'),
        pair(stat('Coverage', sc.coverage.answered_correct + ' of ' + sc.coverage.definite, 'answered and correct'), 'Share of the items that clearly had an answer which the agent answered correctly.'),
        pair(stat('Pass rate', pct(sc.pass_rate), 'pass@' + sc.k + (sc.pass_k !== null && sc.k > 1 ? ', pass^k ' + pct(sc.pass_k) : '')), 'Share of cases whose outcome matched what was expected.'),
        pair(stat('Abstention', 'precision ' + pct(sc.abstention.precision, 0), 'recall ' + pct(sc.abstention.recall, 0)), 'Precision: of what it handed back, how much it should have. Recall: of what it should hand back, how much it did.'),
        pair(stat('Cost and speed', sc.cost_usd + ' USD', 'p50 ' + sc.latency_ms.p50 + ' ms, p95 ' + sc.latency_ms.p95 + ' ms'), SCRIPTED ? 'Scripted run: cost and latency are recorded plumbing, not live figures.' : 'Measured on this run.'))], 'h-other'),
      section('Hard zeros', [
        h('p', { class: 'muted' }, 'Any non-zero count here means "not demonstrated", whatever the rates are. A confident wrong answer where the agent should have abstained is one of them.'),
        table(['Hard zero', 'Meaning', 'Count', 'Status'], sc.hard_zero_defs.map(function (d) {
          var c = has(sc.hard_zeros, d.key) ? sc.hard_zeros[d.key] : 0;
          var ex = sc.hard_zero_exposure && has(sc.hard_zero_exposure, d.key) ? sc.hard_zero_exposure[d.key] : null;
          return h('tr', null, td(code(d.key)), td(d.meaning), td(String(c) + (ex ? ' (' + ex.exposed + ' of ' + ex.of + ' cases exposed)' : '')), td(c ? chip('BREACHED', 'bad') : chip('clear', 'ok')));
        }), 'Hard zeros')
      ], 'h-hz'),
      h('p', { class: 'muted foot' }, 'Frozen set: lock ', code(short(sc.lock_sha256), sc.lock_sha256), '. Gate: green ≤ ' + pct(sc.thresholds.green_max_upper, 0) + ' and amber ≤ ' + pct(sc.thresholds.amber_max_upper, 0) + ' on the 95 % upper bound.')
    ];
  }

  // ---- page: registry and readiness
  function pageRegistry() {
    var r = D.registry;
    return [
      pageHead('Registry', 'Who owns this agent, what it may call, and what is ready.'),
      section('Agent', h('dl', { class: 'kv kv-wide' },
        h('dt', null, 'Agent'), h('dd', null, code(r.agent_id)), h('dt', null, 'Owner role'), h('dd', null, r.owner_role),
        h('dt', null, 'Autonomy level'), h('dd', null, chip(r.level, 'info'), ' ', r.level_note || (r.level === 'shadow' ? 'proposes only, nothing is released' : 'suggests; a person approves every release')),
        h('dt', null, 'Demo'), h('dd', null, D.demo.title + ' v' + D.demo.version)), 'h-agent'),
      section('Tools', table(['Tool', 'Risk', 'Approval', 'Rules'], r.tools.map(function (t) {
        return h('tr', null, td(code(t.name)), td(chip(t.risk)), td(t.needs_approval ? chip('required', 'warn') : chip('not needed', 'neutral')), td(t.rules.length ? t.rules.map(function (x) { return [code(x), ' ']; }) : '—'));
      }), 'Tools'), 'h-tools'),
      section('Readiness', [FIXTURE ? h('p', { class: 'muted' }, 'This export is a fixture: readiness was not evaluated, so every check shows n/a.') : null,
        table(['Check', 'Status', 'Evidence'], r.readiness.map(function (x) { return h('tr', null, td(x.check), td(chip(x.status)), td(x.evidence)); }), 'Readiness checks')], 'h-ready'),
      section('What is faked', h('ul', { class: 'plain' }, [h('li', null, 'Data: synthetic.'), h('li', null, 'Model: ' + D.run.model + ' (' + D.run.mode + ').'), h('li', null, 'Adapters: ' + D.honesty.adapters + '.')].concat(D.honesty.notes.map(function (n) { return h('li', null, n); }))), 'h-fake')
    ];
  }


  // ---- page: controls (schema minor 1 blocks; each is shown only when the export carries it)
  function pageControls() {
    var res = [pageHead('Controls', 'Message ledger, tenant isolation, load profile, read log and provider terms, exactly as the export states them.')];
    var any = false;
    if (D.tenant_id) { any = true; }
    if (D.ledger) {
      any = true;
      var L = D.ledger, bad = L.non_terminal + L.missing + L.duplicated_effects + L.reconciliation_gap > 0;
      res.push(section('Message ledger (no message lost or double-applied)', [
        h('p', { class: 'note note-' + (bad ? 'bad' : 'ok'), role: bad ? 'alert' : 'note' }, bad ? 'LEDGER BREACH: a message is missing, unfinished, duplicated or unreconciled.' : (FIXTURE ? 'No breach is stated in this fixture. These counts come from invented sample data and were not measured.' : 'Every received message ended in a recorded terminal state; no gap against the source; no duplicated effect.')),
        h('dl', { class: 'kv kv-wide' },
          h('dt', null, 'Scope'), h('dd', null, L.scope === 'load_run' ? 'counted by a load run (raw report hash below)' : 'counted from the cases in this export'),
          h('dt', null, 'Received'), h('dd', null, String(L.received)),
          h('dt', null, 'Processed'), h('dd', null, String(L.by_state.processed)),
          h('dt', null, 'Abstained'), h('dd', null, String(L.by_state.abstained)),
          h('dt', null, 'Dead-lettered'), h('dd', null, String(L.by_state.dead_lettered)),
          h('dt', null, 'Rejected'), h('dd', null, String(L.by_state.rejected)),
          h('dt', null, 'Not finished'), h('dd', null, String(L.non_terminal)),
          h('dt', null, 'Missing against source'), h('dd', null, String(L.missing)),
          h('dt', null, 'Duplicated effects'), h('dd', null, String(L.duplicated_effects)),
          h('dt', null, 'Reconciliation gap'), h('dd', null, String(L.reconciliation_gap)),
          h('dt', null, 'Raw report'), h('dd', null, L.report_sha256 ? code(short(L.report_sha256), L.report_sha256) : 'none (counted from this export)'))
      ], 'h-ledger'));
    }
    if (D.segregation) {
      any = true;
      var S = D.segregation, cn = S.canary;
      res.push(section('Tenant and client segregation', [
        h('dl', { class: 'kv kv-wide' },
          h('dt', null, 'Tenant'), h('dd', null, code(D.tenant_id || '—')),
          h('dt', null, 'Namespace (store, index, cache, prompts)'), h('dd', null, code(S.namespace)),
          h('dt', null, 'Cross-tenant canary test'), h('dd', null, cn.ran ? (cn.foreign_hits ? chip('foreign canary found: ' + cn.foreign_hits, 'bad') : chip('ran, 0 foreign hits', 'ok')) : chip('not run', 'warn')))
      ], 'h-seg'));
    }
    if (D.load_profile) {
      any = true;
      var P = D.load_profile;
      res.push(section('Load profile', [
        h('p', { class: 'note note-' + (P.label === 'measured' ? 'ok' : 'warn') }, P.label === 'measured' ? 'MEASURED: taken from a recorded load report.' : 'ASSUMED: a default profile, not this client’s real peak.'),
        h('dl', { class: 'kv kv-wide' }, h('dt', null, 'Profile'), h('dd', null, code(P.name)), h('dt', null, 'Source'), h('dd', null, P.source),
          h('dt', null, 'Raw report'), h('dd', null, P.report_sha256 ? code(short(P.report_sha256), P.report_sha256) : 'none'))
      ], 'h-load'));
    }
    if (D.read_log) {
      any = true;
      var R = D.read_log;
      res.push(section('Read log of client data', [
        h('p', null, R.n_reads + ' reads, ' + R.rows_total + ' rows; ' + (R.client_reviewable ? 'reviewable by the client.' : 'not yet reviewable by the client.')),
        table(['Purpose', 'Reads', 'Rows'], R.by_purpose.map(function (x) { return h('tr', null, td(x.purpose), td(String(x.n_reads)), td(String(x.rows))); }), 'Reads by purpose'),
        h('p', { class: 'muted' }, 'Log hash: ', R.log_sha256 ? code(short(R.log_sha256), R.log_sha256) : 'none recorded')
      ], 'h-read'));
    }
    if (D.provider_terms) {
      any = true;
      var T = D.provider_terms;
      res.push(section('Model provider terms', [
        T.no_training_setting === null ? h('p', { class: 'note note-warn' }, 'Provider no-training setting: NOT RECORDED. It is checked and dated before any client data is used.') :
          h('p', { class: 'note note-ok' }, 'No-training setting: ' + T.no_training_setting + ', checked on ' + T.checked_on + '.'),
        h('p', { class: 'muted' }, 'Provider: ' + (T.provider === null ? 'not named' : T.provider))
      ], 'h-terms'));
    }
    if (D.amounts) {
      any = true;
      res.push(section('Money, per currency', [
        table(['Kind', 'Currency', 'Amount', 'Items'], D.amounts.map(function (x) { return h('tr', null, td(words(x.kind)), td(code(x.currency)), td(money(x.minor, x.currency)), td(String(x.n))); }), 'Amounts per currency'),
        h('p', { class: 'muted' }, 'Currencies are never added together.')
      ], 'h-amounts'));
    }
    if (!any) res.push(h('p', { class: 'muted' }, 'This export carries none of these blocks (it was written before schema minor 1).'));
    return res;
  }

  // ---- router, theme, focus
  var ROUTES = Object.create(null);
  ROUTES.approvals = pageApprovals; ROUTES.run = pageRun; ROUTES.scorecard = pageScorecard; ROUTES.registry = pageRegistry; ROUTES.controls = pageControls;
  var TITLES = { approvals: 'Approvals', run: 'Run and audit', scorecard: 'Scorecard', registry: 'Registry', controls: 'Controls' };
  function safeDecode(s) { try { return decodeURIComponent(s); } catch (e) { return ''; } }
  function parse() {
    var raw = location.hash.replace(/^#\/?/, ''), q = raw.split('?'), params = Object.create(null);
    (q[1] || '').split('&').forEach(function (p) { if (p) { var kv = p.split('='); params[safeDecode(kv[0])] = safeDecode(kv[1] || ''); } });
    return { name: has(ROUTES, q[0]) ? q[0] : 'approvals', params: params };
  }
  var themeOverride = null;
  function themeParam() { return themeOverride ? '&theme=' + themeOverride : ''; }
  function applyTheme(p) {
    var t = has(p, 'theme') ? p.theme : themeOverride;
    if (t === 'dark' || t === 'light') { root.dataset.theme = t; themeOverride = t; }
    else if (!root.dataset.theme) root.dataset.theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    document.getElementById('theme-btn').setAttribute('aria-pressed', root.dataset.theme === 'dark' ? 'true' : 'false');
  }
  function paintChrome() {
    document.getElementById('banner-text').textContent = D.banner;
    document.getElementById('demo-title').textContent = D.demo.title;
    document.getElementById('honest-line').textContent = 'Synthetic data. Model: ' + D.run.model + ' (' + D.run.mode + '). Adapters: ' + D.honesty.adapters + '.' + (SCRIPTED ? ' ' + SCRIPTED_TEXT + '.' : '');
    var rib = document.getElementById('ribbon'), msgs = [];
    if (FIXTURE) msgs.push('FIXTURE, not a run: invented sample data for rendering. Nothing here was measured.');
    if (!CHAIN_OK) msgs.push('The audit chain does not verify. ' + chainLine());
    if (HONEST.indexOf('ledger_breach') >= 0) msgs.push('LEDGER BREACH: see Controls.');
    if (HONEST.indexOf('canary_breach') >= 0) msgs.push('TENANT CANARY BREACH: see Controls.');
    root.dataset.honest = HONEST.concat(CHAIN_OK ? [] : ['chain_bad']).join(',');
    if (msgs.length) { rib.textContent = msgs.join(' '); rib.hidden = false; rib.className = 'ribbon' + (CHAIN_OK ? '' : ' ribbon-bad'); } else rib.hidden = true;
  }
  var lastRoute = null;
  function render() {
    var active = document.activeElement && document.activeElement.id ? document.activeElement.id : null;
    var r = parse();
    applyTheme(r.params);
    try {
      setKids(main, ROUTES[r.name](r.params));
    } catch (err) {
      setKids(main, h('div', { class: 'note note-bad', role: 'alert' }, 'This page could not be shown (' + (err && err.name ? err.name : 'error') + '). The export may be malformed. Other pages may still work.'));
    }
    document.title = D.demo.title + ' - ' + TITLES[r.name] + ' - Hadavin demo console';
    document.querySelectorAll('nav a').forEach(function (a) {
      if (a.getAttribute('data-route') === r.name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    if (lastRoute !== null && lastRoute !== r.name) { var t = document.getElementById('page-title'); if (t) t.focus(); window.scrollTo(0, 0); }
    else if (active) { var el = document.getElementById(active); if (el) el.focus(); }
    lastRoute = r.name;
    root.dataset.rendered = r.name;
    if (has(r.params, 'do') && !root.dataset.did) {
      var want = r.params['do'], ids = { verify: 'btn-verify', tamper: 'btn-tamper', paste: 'btn-paste', all: 'show-all' };
      var btn = has(ids, want) ? document.getElementById(ids[want]) : document.querySelector('button[data-act="' + want.replace(/[^a-z]/g, '') + '"]');
      if (btn) { root.dataset.did = want; btn.click(); }
    }
  }
  document.getElementById('theme-btn').addEventListener('click', function () {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    themeOverride = root.dataset.theme;
    applyTheme({});
  });
  document.getElementById('skip').addEventListener('click', function () { main.focus(); });
  paintChrome();
  window.addEventListener('hashchange', render);
  render();
})();
