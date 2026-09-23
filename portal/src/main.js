import './styles.css';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.docx'];
const STAGES = [
  { key: 'upload', number: '01', label: 'Upload' },
  { key: 'security', number: '02', label: 'Security & Format' },
  { key: 'extract', number: '03', label: 'Extract & Review' },
  { key: 'rules', number: '04', label: 'Business Rules' },
  { key: 'result', number: '05', label: 'Result' },
];

const state = {
  file: null,
  jobId: null,
  jobData: null,
  activeStage: 0,
  uploading: false,
  polling: false,
  error: '',
};

const app = document.querySelector('#app');

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return 'Not available';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function extensionOf(name = '') {
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index).toLowerCase() : '';
}

function isTerminal(job) {
  return job?.status === 'COMPLETED' || job?.status === 'FAILED';
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const message = body?.detail || body?.error?.message || body?.details || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body;
}

async function uploadFile(file) {
  const form = new FormData();
  form.append('file', file);
  return request('/api/v1/job-letters', { method: 'POST', body: form });
}

async function getJob(jobId) {
  return request(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
}

function stageFromData(jobData) {
  if (!jobData) return 0;
  if (jobData.job?.status === 'FAILED') return 4;
  if (jobData.job?.status === 'COMPLETED') return 4;
  const audit = jobData.audit || [];
  if (audit.some((item) => item.step === 'BUSINESS_RULE_VALIDATION')) return 3;
  if (audit.some((item) => item.step === 'LLM_FIELD_EXTRACTION')) return 2;
  if (audit.some((item) => item.step === 'FILE_FORMAT_VALIDATION')) return 1;
  return 1;
}

function auditFor(jobData, steps) {
  return (jobData?.audit || []).filter((item) => steps.includes(item.step));
}

function statusTone(outcome) {
  if (outcome === 'PASS') return 'success';
  if (outcome === 'FAIL') return 'danger';
  return 'warning';
}

function statusLabel(outcome) {
  return outcome === 'PASS' ? 'Passed' : outcome === 'FAIL' ? 'Failed' : 'Review';
}

function iconFor(outcome) {
  return outcome === 'PASS' ? '✓' : outcome === 'FAIL' ? '×' : '•';
}

function renderHeader() {
  return `
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">JL</div>
        <div><strong>Job Letter</strong><span>Workflow Studio</span></div>
      </div>
      <button class="text-button" data-action="reset" ${state.uploading ? 'disabled' : ''}>Start over <span aria-hidden="true">↗</span></button>
    </header>`;
}

function renderStepper() {
  return `<nav class="stepper" aria-label="Workflow stages">
    ${STAGES.map((stage, index) => `
      <button class="step ${index === state.activeStage ? 'active' : ''} ${index < state.activeStage ? 'complete' : ''}" data-stage="${index}" ${!state.jobId || index > state.activeStage ? 'disabled' : ''}>
        <span class="step-number">${index < state.activeStage ? '✓' : stage.number}</span>
        <span class="step-label">${stage.label}</span>
      </button>${index < STAGES.length - 1 ? '<span class="step-line" aria-hidden="true"></span>' : ''}
    `).join('')}
  </nav>`;
}

function renderUpload() {
  const file = state.file;
  const message = state.error ? `<div class="alert danger-alert"><span>!</span>${escapeHtml(state.error)}</div>` : '';
  return `
    <section class="hero-copy">
      <p class="eyebrow">Document intake</p>
      <h1>Upload the <em>job letter</em></h1>
      <p>Start with the document you want to verify. We will run the existing security, extraction, and business-rule workflow for you.</p>
    </section>
    ${message}
    <section class="upload-layout">
      <div class="upload-card">
        <div class="upload-zone ${file ? 'has-file' : ''}" data-dropzone tabindex="0" role="button" aria-label="Choose a job letter file">
          ${file ? `
            <div class="file-symbol">${escapeHtml(extensionOf(file.name).slice(1).toUpperCase() || 'FILE')}</div>
            <div class="file-copy"><strong>${escapeHtml(file.name)}</strong><span>${escapeHtml(extensionOf(file.name).slice(1).toUpperCase())} · ${formatBytes(file.size)}</span></div>
            <button class="icon-button" data-action="remove-file" title="Remove file" aria-label="Remove file">×</button>
          ` : `
            <div class="upload-icon" aria-hidden="true">↑</div>
            <strong>Drop a job letter here</strong>
            <span>or choose a file from your computer</span>
            <button class="browse-button" data-action="browse">Browse files</button>
          `}
          <input id="file-input" type="file" accept=".pdf,.jpg,.jpeg,.png,.docx" hidden />
        </div>
        <div class="format-row"><span>Accepted formats</span><strong>PDF · JPEG · PNG · DOCX</strong><span>Max 10 MB</span></div>
      </div>
      <aside class="intake-note">
        <div class="note-icon">i</div>
        <div><strong>What happens next</strong><p>Your file is queued, scanned, validated, and processed through the backend workflow. You can watch each stage as it completes.</p></div>
      </aside>
    </section>
    <div class="action-row"><span class="quiet-text">${file ? 'Ready to send for verification' : 'Select a document to continue'}</span><button class="primary-button" data-action="upload" ${!file || state.uploading ? 'disabled' : ''}>${state.uploading ? '<span class="spinner"></span> Uploading…' : 'Continue to verification <span>→</span>'}</button></div>`;
}

function renderAuditCard(item) {
  return `<div class="audit-row"><span class="status-dot ${statusTone(item.outcome)}">${iconFor(item.outcome)}</span><div class="audit-main"><strong>${escapeHtml(item.step.replaceAll('_', ' '))}</strong><span>${escapeHtml(item.message || 'No additional details')}</span></div><span class="pill ${statusTone(item.outcome)}">${statusLabel(item.outcome)}</span></div>`;
}

function renderProcessing() {
  const job = state.jobData?.job;
  const audits = state.jobData?.audit || [];
  const recent = audits.slice(-4).reverse();
  return `<section class="processing-panel"><div class="processing-orbit"><span></span></div><p class="eyebrow">Live workflow</p><h1>${job?.status === 'FAILED' ? 'Verification stopped' : 'Processing your document'}</h1><p>${job?.status === 'FAILED' ? 'The backend returned a failure. Review the details below.' : 'The backend is working through the document. This page will update automatically.'}</p><div class="processing-status"><span class="spinner"></span><strong>${escapeHtml(job?.details || 'Working through the verification stages…')}</strong></div><div class="audit-list compact">${recent.length ? recent.map(renderAuditCard).join('') : '<div class="skeleton-row"></div><div class="skeleton-row"></div>'}</div>${job?.status === 'FAILED' ? `<button class="secondary-button" data-action="view-result">View failure details <span>→</span></button>` : ''}</section>`;
}

function renderJobSummary() {
  const job = state.jobData?.job;
  return `<div class="job-summary"><span>Job ID <strong>${escapeHtml(job?.job_id || state.jobId)}</strong></span><span>Submitted <strong>${formatDate(job?.submitted_at)}</strong></span><span>Updated <strong>${formatDate(job?.updated_at)}</strong></span></div>`;
}

function renderSecurity() {
  const items = auditFor(state.jobData, ['SECURITY_SCAN', 'FILE_FORMAT_VALIDATION', 'FILE_METADATA_EXTRACTION']);
  return `<section class="content-heading"><p class="eyebrow">Stage 02 · Security & format</p><h1>Document cleared for review</h1><p>The backend has completed its security scan and format validation. Here is the recorded audit trail.</p></section>${renderJobSummary()}<section class="audit-list">${items.length ? items.map(renderAuditCard).join('') : renderProcessing()}</section><div class="action-row"><span class="quiet-text">${items.length} backend checks recorded</span><button class="primary-button" data-action="next">Continue to extraction <span>→</span></button></div>`;
}

function fieldLabel(key) {
  return key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function extractedDataFor(jobData) {
  const resultData = jobData?.result?.extracted_data;
  if (resultData && Object.keys(resultData).length) return resultData;

  const extractionAudit = (jobData?.audit || []).find((item) => item.step === 'LLM_FIELD_EXTRACTION');
  const fields = extractionAudit?.data?.fields;
  if (!fields) return {};
  const salary = fields.income_amount == null
    ? null
    : `${fields.income_currency || ''} ${Number(fields.income_amount).toLocaleString()}`.trim();
  return {
    employee_name: fields.employee_name,
    employer_name: fields.employer_name,
    job_title: fields.job_title,
    salary,
    income_amount: fields.income_amount,
    income_currency: fields.income_currency,
    income_frequency: fields.income_frequency,
    job_letter_address: fields.employer_address,
    document_date: fields.letter_date,
    employment_start_date: fields.employment_start_date,
    addressed_to: fields.addressed_to,
  };
}

function renderExtract() {
  const data = state.jobData?.result?.extracted_data || {};
  const entries = Object.entries(data);
  return `<section class="content-heading"><p class="eyebrow">Stage 03 · Extract & review</p><h1>Review extracted information</h1><p>These values were returned by the backend field extraction step. Empty values remain visible so missing information is easy to spot.</p></section>${renderJobSummary()}<section class="field-grid">${entries.length ? entries.map(([key, value]) => `<div class="field-card ${value == null ? 'missing' : ''}"><span>${escapeHtml(fieldLabel(key))}</span><strong>${value == null || value === '' ? 'Not extracted' : escapeHtml(value)}</strong>${value == null || value === '' ? '<small>Review required</small>' : '<small>Backend extracted value</small>'}</div>`).join('') : '<div class="empty-state">Extraction data is not available for this job.</div>'}</section><div class="action-row"><span class="quiet-text">${entries.length} fields returned by the backend</span><button class="primary-button" data-action="next">Review business rules <span>→</span></button></div>`;
}

function renderRules() {
  const rules = state.jobData?.result?.validation_summary || [];
  const audits = auditFor(state.jobData, ['BUSINESS_RULE_VALIDATION', 'SAGICOR_BANK_CHECK', 'LETTER_VALIDITY_CHECK', 'INCOME_VALIDATION']);
  const rows = rules.length ? rules.map((rule) => `<div class="rule-row"><span class="status-dot ${rule.passed ? 'success' : 'danger'}">${rule.passed ? '✓' : '×'}</span><div class="audit-main"><strong>${escapeHtml(rule.rule || 'Business rule')}</strong><span>${escapeHtml(rule.detail || 'No detail returned')}</span></div><span class="pill ${rule.passed ? 'success' : 'danger'}">${rule.passed ? 'Passed' : 'Failed'}</span></div>`).join('') : audits.map(renderAuditCard).join('');
  return `<section class="content-heading"><p class="eyebrow">Stage 04 · Business rules</p><h1>Validation checks</h1><p>Every rule shown here comes from the job's validation summary or audit trail.</p></section>${renderJobSummary()}<section class="rules-card"><div class="card-heading"><strong>Business rule validation</strong><span>${rules.length || audits.length} checks</span></div>${rows || '<div class="empty-state">No business-rule summary was returned.</div>'}</section><div class="action-row"><span class="quiet-text">The final status is ready to view</span><button class="primary-button" data-action="next">View final result <span>→</span></button></div>`;
}

function renderFinalVerification(result, fields) {
  const rules = result?.validation_summary || [];
  const rule = (number) => rules.find((item) => item.rule === `Rule ${number}`);
  const failedCode = result?.error?.code;
  const rows = [
    { label: 'Bank name', detail: fields.addressed_to || 'Not extracted', check: rule(1), fallbackPassed: Boolean(fields.addressed_to) && failedCode !== 'NOT_ADDRESSED_TO_SAGICOR_BANK' },
    { label: 'Document date / validity', detail: rule(2)?.detail || (fields.document_date ? `Letter date: ${fields.document_date} · Maximum age: 90 days` : 'Letter date not extracted'), check: rule(2), fallbackPassed: Boolean(fields.document_date) && failedCode !== 'JOB_LETTER_EXPIRED' },
    { label: 'Income stated', detail: rule(3)?.detail || (fields.salary ? `${fields.salary}${fields.income_frequency ? ` ${fields.income_frequency}` : ''}` : 'Income not extracted'), check: rule(3), fallbackPassed: Boolean(fields.salary) && failedCode !== 'INCOME_NOT_STATED' },
    { label: 'Employment start date present', detail: rule(4)?.detail || fields.employment_start_date || 'Employment start date not extracted', check: rule(4), fallbackPassed: Boolean(fields.employment_start_date) && failedCode !== 'EMPLOYMENT_START_DATE_MISSING' },
  ];
  return `<section class="final-verification"><div class="card-heading"><strong>Required verification details</strong><span>Final result</span></div>${rows.map((item) => {
    const passed = item.check?.passed ?? item.fallbackPassed;
    return `<div class="verification-row"><span class="status-dot ${passed ? 'success' : 'warning'}">${passed ? '✓' : '•'}</span><div class="audit-main"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span></div><span class="pill ${passed ? 'success' : 'warning'}">${passed ? 'Passed' : 'Review'}</span></div>`;
  }).join('')}</section>`;
}

function renderResult() {
  const job = state.jobData?.job;
  const result = state.jobData?.result;
  const failed = job?.status === 'FAILED' || result?.status === 'FAILED';
  const fields = extractedDataFor(state.jobData);
  const fraud = result?.fraud_detection;
  return `<section class="result-banner ${failed ? 'failed' : ''}"><div class="result-symbol">${failed ? '×' : '✓'}</div><div><p class="eyebrow">Stage 05 · Final result</p><h1>${failed ? 'Verification requires attention' : 'Job letter verified'}</h1><p>${escapeHtml(result?.details || job?.details || 'No final result details were returned.')}</p></div><span class="result-status">${failed ? 'FAILED' : 'COMPLETED'}</span></section>${renderJobSummary()}${failed ? `<section class="error-card"><span class="error-code">${escapeHtml(result?.error?.code || job?.error_code || 'PROCESSING_ERROR')}</span><h2>${escapeHtml(result?.error?.message || job?.details || 'The document could not be verified.')}</h2><p>${escapeHtml(result?.details || '')}</p></section>` : `<section class="summary-grid"><div class="summary-card"><span>Document status</span><strong>Verified</strong><small>All required workflow steps completed</small></div><div class="summary-card"><span>Security status</span><strong>Cleared</strong><small>Security and format checks passed</small></div><div class="summary-card"><span>Business rules</span><strong>${(result?.validation_summary || []).length} passed</strong><small>Validation summary returned by backend</small></div></section><section class="result-detail"><div class="card-heading"><strong>Extracted information</strong><span>${Object.keys(fields).length} values</span></div><div class="mini-fields">${Object.entries(fields).map(([key, value]) => `<div><span>${escapeHtml(fieldLabel(key))}</span><strong>${value == null || value === '' ? 'Not extracted' : escapeHtml(value)}</strong></div>`).join('')}</div></section>${fraud?.flagged ? `<section class="alert warning-alert"><span>!</span><div><strong>Risk indicators flagged for review</strong><p>${fraud.indicators?.map((item) => escapeHtml(item.description)).join(' · ')}</p></div></section>` : ''}` }<div class="action-row"><span class="quiet-text">Job ${escapeHtml(job?.job_id || state.jobId)} · ${formatDate(job?.updated_at)}</span><button class="primary-button" data-action="reset">Process another document <span>↗</span></button></div>`;
}

function render() {
  const isProcessing = state.polling && !isTerminal(state.jobData?.job);
  let content = state.activeStage === 0 && !state.jobId ? renderUpload() : isProcessing ? renderProcessing() : [renderUpload, renderSecurity, renderExtract, renderRules, renderResult][state.activeStage]();
  if (state.activeStage === 4 && !isProcessing) {
    content += renderFinalVerification(state.jobData?.result, extractedDataFor(state.jobData));
  }
  app.innerHTML = `<div class="app-shell">${renderHeader()}${renderStepper()}<main>${content}</main><footer><span>Job Letter Workflow Studio</span><span>Connected to local verification service</span></footer></div>`;
  bindEvents();
}

function validateFile(file) {
  if (!file) return 'Choose a file to continue.';
  if (!ACCEPTED_EXTENSIONS.includes(extensionOf(file.name))) return 'That file type is not supported. Choose PDF, JPEG, PNG, or DOCX.';
  if (file.size === 0) return 'This file is empty. Choose a document with content.';
  if (file.size > MAX_UPLOAD_BYTES) return 'This file is larger than the backend limit of 10 MB.';
  return '';
}

function selectFile(file) {
  state.error = validateFile(file);
  state.file = state.error ? null : file;
  render();
}

async function handleUpload() {
  if (!state.file || state.uploading) return;
  state.uploading = true;
  state.error = '';
  render();
  try {
    const response = await uploadFile(state.file);
    state.jobId = response.job_id;
    state.activeStage = 1;
    state.polling = true;
    state.jobData = { job: response, audit: [], result: null };
    render();
    await pollJob();
  } catch (error) {
    console.error(error);
    state.error = error.message || 'The document could not be uploaded.';
    state.uploading = false;
    state.polling = false;
    state.jobId = null;
    render();
  }
}

async function pollJob() {
  try {
    const data = await getJob(state.jobId);
    state.jobData = data;
    state.activeStage = stageFromData(data);
    state.uploading = false;
    render();
    if (!isTerminal(data.job)) {
      window.setTimeout(pollJob, 1400);
    } else {
      state.polling = false;
      state.activeStage = 4;
      render();
    }
  } catch (error) {
    console.error(error);
    state.polling = false;
    state.uploading = false;
    state.error = 'We could not reach the verification service. Check that the backend is running and try again.';
    render();
  }
}

function reset() {
  state.file = null;
  state.jobId = null;
  state.jobData = null;
  state.activeStage = 0;
  state.uploading = false;
  state.polling = false;
  state.error = '';
  render();
}

function bindEvents() {
  const input = document.querySelector('#file-input');
  document.querySelectorAll('[data-stage]').forEach((button) => button.addEventListener('click', () => {
    state.activeStage = Number(button.dataset.stage);
    render();
  }));
  document.querySelectorAll('[data-action="browse"]').forEach((button) => button.addEventListener('click', (event) => { event.stopPropagation(); input?.click(); }));
  input?.addEventListener('change', (event) => selectFile(event.target.files[0]));
  document.querySelector('[data-dropzone]')?.addEventListener('dragover', (event) => { event.preventDefault(); event.currentTarget.classList.add('dragging'); });
  document.querySelector('[data-dropzone]')?.addEventListener('dragleave', (event) => event.currentTarget.classList.remove('dragging'));
  document.querySelector('[data-dropzone]')?.addEventListener('drop', (event) => { event.preventDefault(); event.currentTarget.classList.remove('dragging'); selectFile(event.dataTransfer.files[0]); });
  document.querySelector('[data-dropzone]')?.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') input?.click(); });
  document.querySelector('[data-action="remove-file"]')?.addEventListener('click', reset);
  document.querySelector('[data-action="upload"]')?.addEventListener('click', handleUpload);
  document.querySelector('[data-action="reset"]')?.addEventListener('click', reset);
  document.querySelector('[data-action="next"]')?.addEventListener('click', () => { state.activeStage = Math.min(4, state.activeStage + 1); render(); });
  document.querySelector('[data-action="view-result"]')?.addEventListener('click', () => { state.activeStage = 4; render(); });
}

render();
