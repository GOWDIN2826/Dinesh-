import './styles.css';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.docx'];
const RULES = [
  { rule: 'Rule 1', label: 'Letter addressed to Sagicor Bank' },
  { rule: 'Rule 2', label: 'Letter date within the allowed 90-day period' },
  { rule: 'Rule 3', label: 'Income amount, currency, and frequency stated' },
  { rule: 'Rule 4', label: 'Employment start date is present' },
  { rule: 'Rule 5', label: 'Job title or position is stated' },
];

const state = {
  file: null,
  result: null,
  uploading: false,
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

function extensionOf(name = '') {
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index).toLowerCase() : '';
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function validateFile(file) {
  if (!file) return 'Choose a file to verify.';
  if (!ACCEPTED_EXTENSIONS.includes(extensionOf(file.name))) {
    return 'Unsupported file type. Choose PDF, JPEG, PNG, or DOCX.';
  }
  if (!file.size) return 'This file is empty.';
  if (file.size > MAX_UPLOAD_BYTES) return 'This file exceeds the 10 MB upload limit.';
  return '';
}

async function uploadFile(file) {
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`${API_BASE}/api/v1/job-letters`, { method: 'POST', body: form });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The verification service returned an invalid response (${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.details || `Verification failed (${response.status}).`);
  }
  return payload;
}

function renderRuleChecks(result) {
  const summaries = result?.validation_summary || [];
  const checks = RULES.map((item) => {
    const summary = summaries.find((candidate) => candidate.rule === item.rule);
    const passed = summary?.passed === true;
    const detail = summary?.detail || (
      result?.details
        ? `Not evaluated: ${result.details}`
        : 'Not evaluated because required document data was unavailable.'
    );
    return `<li class="verification-rule ${passed ? 'passed' : 'failed'}">
      <span class="rule-value">${passed ? 'True' : 'False'}</span>
      <div><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(detail)}</p></div>
    </li>`;
  }).join('');

  return `<section class="verification-card" aria-labelledby="rules-heading">
    <div class="verification-heading">
      <div><p class="page-kicker">Business rules</p><h2 id="rules-heading">Verification checks</h2></div>
      <span class="rules-count">${summaries.length} of 5 evaluated</span>
    </div>
    <ol class="verification-rules">${checks}</ol>
  </section>`;
}

function renderExtractedFields(result) {
  const fields = result?.extracted_data;
  if (!fields) return '';
  const entries = [
    ['Employee', fields.employee_name],
    ['Employer', fields.employer_name],
    ['Job title', fields.job_title],
    ['Addressed to', fields.addressed_to],
    ['Letter date', fields.document_date],
    ['Employment start date', fields.employment_start_date],
    ['Income', [fields.salary, fields.income_frequency].filter(Boolean).join(' · ')],
  ].filter(([, value]) => value !== null && value !== undefined && value !== '');

  if (!entries.length) return '';
  return `<section class="extracted-card" aria-labelledby="fields-heading">
    <h2 id="fields-heading">Extracted information</h2>
    <dl>${entries.map(([label, value]) =>
      `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
    ).join('')}</dl>
  </section>`;
}

function renderResult() {
  if (!state.result) return '';
  const summaries = state.result.validation_summary || [];
  const passed = RULES.every((rule) =>
    summaries.some((summary) => summary.rule === rule.rule && summary.passed === true)
  );
  return `<section class="result-section" aria-live="polite">
    <div class="result-heading ${passed ? 'result-success' : 'result-failure'}">
      <div><p class="page-kicker">Overall result</p><h2>${passed ? 'Successful' : 'Failed'}</h2></div>
      <span class="result-status">${passed ? 'SUCCESSFUL' : 'FAILED'}</span>
    </div>
    <p class="job-id">Job ID <code>${escapeHtml(state.result.job_id || 'Unavailable')}</code></p>
    <p class="result-detail">${passed
      ? 'All five verification conditions passed.'
      : 'One or more verification conditions failed or could not be evaluated.'}</p>
    ${state.result.error?.message ? `<p class="result-error">${escapeHtml(state.result.error.message)}</p>` : ''}
    ${renderRuleChecks(state.result)}
    ${renderExtractedFields(state.result)}
  </section>`;
}

function render() {
  const file = state.file;
  app.innerHTML = `<main class="single-page">
    <header class="page-header">
      <p class="page-kicker">Job letter verification</p>
      <h1>Verify a job letter</h1>
      <p>Upload a letter to check it against all five verification rules.</p>
    </header>
    ${state.error ? `<p class="upload-error" role="alert">${escapeHtml(state.error)}</p>` : ''}
    <form class="upload-form" id="upload-form">
      <label class="file-picker ${file ? 'has-file' : ''}" for="file-input">
        <span class="picker-icon" aria-hidden="true">↑</span>
        <span class="picker-copy"><strong>${file ? escapeHtml(file.name) : 'Choose a job letter'}</strong>
        <small>${file ? formatBytes(file.size) : 'PDF, JPEG, PNG, or DOCX · Maximum 10 MB'}</small></span>
        <span class="picker-button">Browse</span>
        <input id="file-input" type="file" accept=".pdf,.jpg,.jpeg,.png,.docx" />
      </label>
      <button class="verify-button" type="submit" ${!file || state.uploading ? 'disabled' : ''}>
        ${state.uploading ? 'Verifying…' : 'Verify document'}
      </button>
    </form>
    ${state.uploading ? '<p class="loading-note" role="status">Checking the document and applying all five rules…</p>' : ''}
    ${renderResult()}
  </main>`;

  document.querySelector('#file-input').addEventListener('change', (event) => {
    const file = event.target.files?.[0] || null;
    const validationError = validateFile(file);
    state.error = validationError;
    state.file = validationError ? null : file;
    state.result = null;
    render();
  });

  document.querySelector('#upload-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const validationError = validateFile(state.file);
    if (validationError || state.uploading) {
      state.error = validationError;
      render();
      return;
    }
    state.uploading = true;
    state.error = '';
    state.result = null;
    render();
    try {
      state.result = await uploadFile(state.file);
    } catch (error) {
      state.error = error.message || 'The document could not be verified.';
    } finally {
      state.uploading = false;
      render();
    }
  });
}

render();
