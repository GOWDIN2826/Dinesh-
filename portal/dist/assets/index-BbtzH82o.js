(function(){const e=document.createElement("link").relList;if(e&&e.supports&&e.supports("modulepreload"))return;for(const i of document.querySelectorAll('link[rel="modulepreload"]'))l(i);new MutationObserver(i=>{for(const a of i)if(a.type==="childList")for(const o of a.addedNodes)o.tagName==="LINK"&&o.rel==="modulepreload"&&l(o)}).observe(document,{childList:!0,subtree:!0});function s(i){const a={};return i.integrity&&(a.integrity=i.integrity),i.referrerPolicy&&(a.referrerPolicy=i.referrerPolicy),i.crossOrigin==="use-credentials"?a.credentials="include":i.crossOrigin==="anonymous"?a.credentials="omit":a.credentials="same-origin",a}function l(i){if(i.ep)return;i.ep=!0;const a=s(i);fetch(i.href,a)}})();const f="",m=10*1024*1024,h=[".pdf",".jpg",".jpeg",".png",".docx"],u=[{rule:"Rule 1",label:"Letter addressed to Sagicor Bank"},{rule:"Rule 2",label:"Letter date within the allowed 90-day period"},{rule:"Rule 3",label:"Income amount, currency, and frequency stated"},{rule:"Rule 4",label:"Employment start date is present"},{rule:"Rule 5",label:"Job title or position is stated"}],r={file:null,result:null,uploading:!1,error:""},g=document.querySelector("#app");function n(t){return String(t??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;")}function v(t=""){const e=t.lastIndexOf(".");return e>=0?t.slice(e).toLowerCase():""}function y(t){if(!t)return"0 B";const e=["B","KB","MB"],s=Math.min(Math.floor(Math.log(t)/Math.log(1024)),e.length-1);return`${(t/1024**s).toFixed(s?1:0)} ${e[s]}`}function d(t){return t?h.includes(v(t.name))?t.size?t.size>m?"This file exceeds the 10 MB upload limit.":"":"This file is empty.":"Unsupported file type. Choose PDF, JPEG, PNG, or DOCX.":"Choose a file to verify."}async function b(t){const e=new FormData;e.append("file",t);const s=await fetch(`${f}/api/v1/job-letters`,{method:"POST",body:e});let l;try{l=await s.json()}catch{throw new Error(`The verification service returned an invalid response (${s.status}).`)}if(!s.ok)throw new Error(l?.detail||l?.details||`Verification failed (${s.status}).`);return l}function $(t){const e=t?.validation_summary||[],s=u.map(l=>{const i=e.find(p=>p.rule===l.rule),a=i?.passed===!0,o=i?.detail||(t?.details?`Not evaluated: ${t.details}`:"Not evaluated because required document data was unavailable.");return`<li class="verification-rule ${a?"passed":"failed"}">
      <span class="rule-value">${a?"True":"False"}</span>
      <div><strong>${n(l.label)}</strong><p>${n(o)}</p></div>
    </li>`}).join("");return`<section class="verification-card" aria-labelledby="rules-heading">
    <div class="verification-heading">
      <div><p class="page-kicker">Business rules</p><h2 id="rules-heading">Verification checks</h2></div>
      <span class="rules-count">${e.length} of 5 evaluated</span>
    </div>
    <ol class="verification-rules">${s}</ol>
  </section>`}function E(t){const e=t?.extracted_data;if(!e)return"";const s=[["Employee",e.employee_name],["Employer",e.employer_name],["Job title",e.job_title],["Addressed to",e.addressed_to],["Letter date",e.document_date],["Employment start date",e.employment_start_date],["Income",[e.salary,e.income_frequency].filter(Boolean).join(" · ")]].filter(([,l])=>l!=null&&l!=="");return s.length?`<section class="extracted-card" aria-labelledby="fields-heading">
    <h2 id="fields-heading">Extracted information</h2>
    <dl>${s.map(([l,i])=>`<div><dt>${n(l)}</dt><dd>${n(i)}</dd></div>`).join("")}</dl>
  </section>`:""}function L(){if(!r.result)return"";const t=r.result.validation_summary||[],e=u.every(s=>t.some(l=>l.rule===s.rule&&l.passed===!0));return`<section class="result-section" aria-live="polite">
    <div class="result-heading ${e?"result-success":"result-failure"}">
      <div><p class="page-kicker">Overall result</p><h2>${e?"Successful":"Failed"}</h2></div>
      <span class="result-status">${e?"SUCCESSFUL":"FAILED"}</span>
    </div>
    <p class="job-id">Job ID <code>${n(r.result.job_id||"Unavailable")}</code></p>
    <p class="result-detail">${e?"All five verification conditions passed.":"One or more verification conditions failed or could not be evaluated."}</p>
    ${r.result.error?.message?`<p class="result-error">${n(r.result.error.message)}</p>`:""}
    ${$(r.result)}
    ${E(r.result)}
  </section>`}function c(){const t=r.file;g.innerHTML=`<main class="single-page">
    <header class="page-header">
      <p class="page-kicker">Job letter verification</p>
      <h1>Verify a job letter</h1>
      <p>Upload a letter to check it against all five verification rules.</p>
    </header>
    ${r.error?`<p class="upload-error" role="alert">${n(r.error)}</p>`:""}
    <form class="upload-form" id="upload-form">
      <label class="file-picker ${t?"has-file":""}" for="file-input">
        <span class="picker-icon" aria-hidden="true">↑</span>
        <span class="picker-copy"><strong>${t?n(t.name):"Choose a job letter"}</strong>
        <small>${t?y(t.size):"PDF, JPEG, PNG, or DOCX · Maximum 10 MB"}</small></span>
        <span class="picker-button">Browse</span>
        <input id="file-input" type="file" accept=".pdf,.jpg,.jpeg,.png,.docx" />
      </label>
      <button class="verify-button" type="submit" ${!t||r.uploading?"disabled":""}>
        ${r.uploading?"Verifying…":"Verify document"}
      </button>
    </form>
    ${r.uploading?'<p class="loading-note" role="status">Checking the document and applying all five rules…</p>':""}
    ${L()}
  </main>`,document.querySelector("#file-input").addEventListener("change",e=>{const s=e.target.files?.[0]||null,l=d(s);r.error=l,r.file=l?null:s,r.result=null,c()}),document.querySelector("#upload-form").addEventListener("submit",async e=>{e.preventDefault();const s=d(r.file);if(s||r.uploading){r.error=s,c();return}r.uploading=!0,r.error="",r.result=null,c();try{r.result=await b(r.file)}catch(l){r.error=l.message||"The document could not be verified."}finally{r.uploading=!1,c()}})}c();
