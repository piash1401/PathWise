/* PathWise workspace: profile (CV import + form), programs, scholarships,
   cost planner, AI counsellor, document review, application tracker.
   Needs app.js loaded first (it provides window.PW). */
(function () {
  if (!window.PW || !document.getElementById('studentProfileForm')) return;
  const { $, $$, api, esc, toast, savedUser, fmtBDT, fmtDate } = window.PW;

  const FLAGS = { Germany: '🇩🇪', UK: '🇬🇧', Canada: '🇨🇦', USA: '🇺🇸' };
  const COUNTRY_NAMES = { Germany: 'Germany', UK: 'United Kingdom', Canada: 'Canada', USA: 'United States' };
  const STATUS_LABELS = {
    shortlisted: 'Shortlisted', preparing: 'Preparing documents', applied: 'Applied',
    interview: 'Interview', offer: 'Offer received', rejected: 'Rejected', withdrawn: 'Withdrawn',
  };

  const state = { profile: null, programs: null, loaded: {}, cvImported: false };

  // ================================================================ PROFILE
  const FIELD_MAP = {
    highest_degree: 'highestDegree', field_of_study: 'fieldOfStudy', institution: 'institution',
    graduation_year: 'graduationYear', grade: 'grade', grade_scale: 'gradeScale',
    english_test: 'englishTest', overall_score: 'overallScore', phone: 'phone',
    work_experience_months: 'workMonths',
  };

  const form = $('#studentProfileForm');
  const message = $('#profileMessage');

  function showMessage(text, ok = false) {
    message.textContent = text;
    message.className = 'form-msg ' + (ok ? 'ok' : 'bad');
  }

  function fillForm(profile) {
    const set = (id, value) => { const el = $('#' + id); if (el) el.value = value ?? ''; };
    set('highestDegree', profile.highest_degree); set('fieldOfStudy', profile.field_of_study);
    set('institution', profile.institution); set('graduationYear', profile.graduation_year);
    set('grade', profile.grade); set('gradeScale', profile.grade_scale);
    set('englishTest', profile.english_test); set('overallScore', profile.overall_score);
    set('annualBudget', profile.annual_budget_bdt); set('savings', profile.savings_bdt ?? 0);
    set('familySupport', profile.family_support_bdt ?? 0); set('educationLoan', profile.education_loan_bdt ?? 0);
    set('phone', profile.phone); set('targetField', profile.target_field);
    set('skills', (profile.skills || []).join(', ')); set('workMonths', profile.work_experience_months ?? 0);
    set('intake', profile.intake_preference);
    const picked = profile.destination_preferences || [];
    $$('input[name="destinationPreferences"]').forEach(i => { i.checked = picked.includes(i.value); });
    $('#profileCompletion').textContent = `${profile.profile_completion}% complete`;
  }

  async function loadProfile(force = false) {
    if (state.loaded.profile && !force) return state.profile;
    const list = await api('/profiles/');
    state.profile = list.length ? list[0] : null;
    state.loaded.profile = true;
    if (state.profile) fillForm(state.profile);
    return state.profile;
  }

  const user = savedUser();
  $('#profileFullName').value = user.full_name || '';
  $('#profileEmail').value = user.email || '';
  $('#setName').textContent = user.full_name || '';
  $('#setEmail').textContent = user.email || '';

  // -- choose: CV or form
  $$('[data-profile-mode]').forEach(card => card.addEventListener('click', () => {
    $$('[data-profile-mode]').forEach(c => c.classList.toggle('active', c === card));
    $('#cvPanel').hidden = card.dataset.profileMode !== 'cv';
    if (card.dataset.profileMode === 'form') $('#highestDegree').focus();
  }));

  // -- CV import
  const cvFile = $('#cvFile');
  const cvDrop = $('#cvDrop');
  cvFile.addEventListener('change', () => {
    $('#cvFileName').textContent = cvFile.files[0] ? cvFile.files[0].name : 'Choose a file or drop it here';
  });
  ['dragenter', 'dragover'].forEach(ev => cvDrop.addEventListener(ev, e => { e.preventDefault(); cvDrop.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach(ev => cvDrop.addEventListener(ev, e => { e.preventDefault(); cvDrop.classList.remove('drag'); }));
  cvDrop.addEventListener('drop', e => {
    if (e.dataTransfer.files[0]) {
      cvFile.files = e.dataTransfer.files;
      $('#cvFileName').textContent = e.dataTransfer.files[0].name;
    }
  });

  $('#cvImportBtn').addEventListener('click', async () => {
    const file = cvFile.files[0];
    const status = $('#cvStatus');
    const banner = $('#cvBanner');
    banner.hidden = true;
    if (!file) { status.textContent = 'Choose a CV file first.'; return; }

    const button = $('#cvImportBtn');
    button.disabled = true;
    status.textContent = 'Reading your CV…';
    try {
      const data = new FormData();
      data.append('file', file);
      const result = await api('/profiles/import-cv/', { method: 'POST', form: data });

      // clear old highlights, then fill what we found
      $$('.field.guess').forEach(f => f.classList.remove('guess'));
      Object.entries(result.fields).forEach(([key, value]) => {
        if (key === 'skills') {
          $('#skills').value = value.join(', ');
          markGuess('skills', result.confidence.skills);
        } else if (FIELD_MAP[key]) {
          const el = $('#' + FIELD_MAP[key]);
          if (key === 'english_test' && !Array.from(el.options).some(o => o.value === value)) return;
          el.value = value;
          markGuess(FIELD_MAP[key], result.confidence[key]);
        }
      });

      state.cvImported = true;
      const missing = result.missing.map(k => k.replace(/_/g, ' ')).join(', ');
      banner.className = 'banner' + (result.missing.length ? '' : ' good');
      banner.innerHTML = `<strong>${esc(result.message)}</strong><br>` +
        'Highlighted boxes are guesses. Please correct them, then add your budget and destinations and press <b>Save profile</b>.' +
        (missing ? `<br>We could not find: ${esc(missing)}.` : '');
      banner.hidden = false;
      status.textContent = '';
      $('#studentProfileForm').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  function markGuess(id, confidence) {
    if (confidence === 'high') return;
    const el = $('#' + id);
    if (el && el.closest('.field')) el.closest('.field').classList.add('guess');
  }

  // -- save
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const destinations = $$('input[name="destinationPreferences"]:checked').map(i => i.value);
    const skills = $('#skills').value.split(',').map(s => s.trim()).filter(Boolean);

    const data = {
      highest_degree: $('#highestDegree').value.trim(),
      field_of_study: $('#fieldOfStudy').value.trim(),
      institution: $('#institution').value.trim(),
      graduation_year: Number($('#graduationYear').value),
      grade: $('#grade').value,
      grade_scale: $('#gradeScale').value,
      english_test: $('#englishTest').value,
      overall_score: $('#overallScore').value || null,
      annual_budget_bdt: $('#annualBudget').value,
      savings_bdt: $('#savings').value || 0,
      family_support_bdt: $('#familySupport').value || 0,
      education_loan_bdt: $('#educationLoan').value || 0,
      destination_preferences: destinations,
      phone: $('#phone').value.trim(),
      target_field: $('#targetField').value.trim(),
      skills,
      work_experience_months: Number($('#workMonths').value || 0),
      intake_preference: $('#intake').value,
    };
    if (state.cvImported) data.profile_source = 'cv';

    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = 'Saving…';
    try {
      const existing = !!state.profile;
      const saved = await api(existing ? '/profiles/me/' : '/profiles/', {
        method: existing ? 'PATCH' : 'POST', body: data,
      });
      state.profile = saved;
      $('#profileCompletion').textContent = `${saved.profile_completion}% complete`;
      $$('.field.guess').forEach(f => f.classList.remove('guess'));
      showMessage('Your profile has been saved.', true);
      state.programs = null;             // matches depend on the profile
      refreshOverview();
    } catch (error) {
      showMessage(error.message);
    } finally {
      button.disabled = false;
      button.textContent = 'Save profile';
    }
  });

  // ============================================================== PROGRAMS
  let programMode = 'all';
  let searchTimer;

  $$('#programMode .seg-btn').forEach(btn => btn.addEventListener('click', () => {
    programMode = btn.dataset.mode;
    $$('#programMode .seg-btn').forEach(b => b.classList.toggle('active', b === btn));
    loadPrograms();
  }));
  $('#programCountry').addEventListener('change', loadPrograms);
  $('#programSearch').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadPrograms, 350); });

  function programCard(opp, match) {
    const verified = opp.verification_status === 'verified' && !opp.is_stale;
    const badges = verified
      ? '<span class="badge verified">Verified</span>'
      : '<span class="badge pending">Not yet verified. Check the source</span>';
    const facts = [
      opp.city ? `📍 ${esc(opp.city)}` : '',
      opp.duration_months ? `${opp.duration_months} months` : '',
      opp.tuition_per_year ? `Tuition ${esc(opp.tuition_per_year)} / year` : 'Tuition: see cost planner',
      opp.deadline ? `Deadline ${esc(fmtDate(opp.deadline))}` : 'Deadline: check the official page',
    ].filter(Boolean).map(f => `<span class="fact">${f}</span>`).join('');

    const score = match
      ? `<div class="prog-score ${match.score >= 70 ? 'score-strong' : match.score < 50 ? 'score-weak' : ''}">
           <b>${match.score}</b><small>${esc(match.label)}</small></div>` : '';
    const reasons = match
      ? `<ul class="reasons">${match.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : '';

    return `<article class="card prog" data-id="${opp.id}">
      <div class="prog-top">
        <div class="prog-flag">${FLAGS[opp.country] || '🌍'}</div>
        <div><h3>${esc(opp.title)}</h3>
          <div class="prog-sub">${esc(opp.institution)} · ${esc(COUNTRY_NAMES[opp.country] || opp.country)}</div>
          <div style="margin-top:8px">${badges}</div></div>
        ${score}
      </div>
      <div class="prog-facts">${facts}</div>
      ${reasons}
      <div class="prog-actions">
        <button class="btn btn-outline btn-sm" data-act="eligibility">Check my eligibility</button>
        <button class="btn btn-outline btn-sm" data-act="cost">Estimate cost</button>
        <button class="btn btn-primary btn-sm" data-act="track">Add to tracker</button>
        <a class="link" href="${esc(opp.source_url)}" target="_blank" rel="noopener noreferrer">Official source ↗</a>
      </div>
      <div class="elig" hidden></div>
    </article>`;
  }

  async function loadPrograms() {
    const list = $('#programList');
    const hint = $('#programHint');
    list.innerHTML = '<div class="card empty">Loading programs…</div>';
    const params = new URLSearchParams({ type: 'program' });
    const country = $('#programCountry').value;
    const q = $('#programSearch').value.trim();
    if (country) params.set('country', country);
    if (q) params.set('q', q);

    try {
      if (programMode === 'recommended') {
        hint.textContent = 'Score = eligibility 40 + budget 25 + destination 15 + field 20. It is guidance, not an admission chance.';
        const rec = await api('/opportunities/recommendations/' + (country ? `?country=${country}` : ''));
        const ids = rec.results.filter(r => !q || (r.title + r.institution).toLowerCase().includes(q.toLowerCase()));
        if (!ids.length) { list.innerHTML = '<div class="card empty">No matches yet. Save your profile and try again.</div>'; return; }
        const all = await api('/opportunities/?' + params);
        const byId = Object.fromEntries(all.map(o => [o.id, o]));
        list.innerHTML = ids.filter(r => byId[r.opportunity_id])
          .map(r => programCard(byId[r.opportunity_id], r)).join('');
      } else {
        hint.textContent = '';
        const programs = await api('/opportunities/?' + params);
        state.programs = programs;
        list.innerHTML = programs.length
          ? programs.map(p => programCard(p)).join('')
          : '<div class="card empty">No programs match your search.</div>';
      }
    } catch (error) {
      list.innerHTML = `<div class="card empty">${esc(error.status === 404
        ? 'Please complete and save your profile first, then come back to see recommendations.' : error.message)}</div>`;
    }
  }

  $('#programList').addEventListener('click', async event => {
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    const card = button.closest('.prog');
    const id = Number(card.dataset.id);

    if (button.dataset.act === 'eligibility') {
      const box = card.querySelector('.elig');
      button.disabled = true;
      try {
        const result = await api(`/opportunities/${id}/check-eligibility/`, { method: 'POST', body: {} });
        box.innerHTML = `<strong style="font-size:12px;color:var(--navy)">Overall: ${esc(result.overall_status)}</strong>` +
          result.criteria_results.map(c => {
            const cls = c.status === 'Not met' ? 'Not' : c.status;
            return `<div class="elig-row"><span class="st ${cls}">${esc(c.status)}</span><span>${esc(c.criterion)}</span></div>`;
          }).join('') +
          '<p class="muted small" style="margin:4px 0 0">“Unknown” means the program rules are not stored yet or need manual checking on the official page.</p>';
        box.hidden = false;
      } catch (error) {
        toast(error.status === 404 ? 'Save your profile first.' : error.message);
      } finally { button.disabled = false; }
    }

    if (button.dataset.act === 'cost') {
      $('#costProgram').value = String(id);
      window.PW.activate('costs');
      runEstimate();
    }

    if (button.dataset.act === 'track') {
      button.disabled = true;
      try {
        await api('/applications/', { method: 'POST', body: { opportunity: id } });
        toast('Added to your application tracker.');
        state.loaded.apps = false;
        button.textContent = 'In tracker ✓';
      } catch (error) {
        toast(error.data && error.data.opportunity ? 'Already in your tracker.' : error.message);
        button.disabled = false;
      }
    }
  });

  // ========================================================== SCHOLARSHIPS
  async function loadScholarships() {
    const list = $('#scholarshipList');
    try {
      const items = await api('/opportunities/?type=scholarship');
      list.innerHTML = items.length ? items.map(s => `
        <article class="card prog">
          <div class="prog-top"><div class="prog-flag">${FLAGS[s.country] || '🌍'}</div>
            <div><h3>${esc(s.title)}</h3><div class="prog-sub">${esc(s.institution)} · ${esc(COUNTRY_NAMES[s.country] || s.country)}</div>
            <div style="margin-top:8px"><span class="badge ${s.verification_status === 'verified' ? 'verified' : 'pending'}">${s.verification_status === 'verified' ? 'Verified' : 'Not yet verified. Check the source'}</span></div></div></div>
          <p style="font-size:12px;line-height:1.7;color:#4c5e73;margin:14px 0 0">${esc(s.description)}</p>
          <p class="muted small" style="margin:8px 0 0">${esc(s.eligibility_criteria)}</p>
          <div class="prog-actions"><a class="link" style="margin-left:0" href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">Official page ↗</a></div>
        </article>`).join('') : '<div class="card empty">No scholarships stored yet.</div>';
    } catch (error) {
      list.innerHTML = `<div class="card empty">${esc(error.message)}</div>`;
    }
  }

  // =============================================================== COSTS
  async function fillCostPrograms() {
    const select = $('#costProgram');
    if (select.options.length > 1) return;
    try {
      const programs = await api('/opportunities/?type=program');
      programs.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.title} — ${p.institution} (${p.country})`;
        select.appendChild(opt);
      });
    } catch (error) { /* the country list still works */ }
  }

  function basisBadge(basis) {
    const label = { official: 'Official', assumption: 'Assumption', program: 'From program' }[basis] || basis;
    return `<span class="badge ${esc(basis)}">${esc(label)}</span>`;
  }

  async function runEstimate() {
    const msg = $('#costMsg');
    const out = $('#costResult');
    const params = new URLSearchParams();
    const program = $('#costProgram').value;
    if (program) params.set('opportunity', program); else params.set('country', $('#costCountry').value);
    if ($('#costCity').value.trim()) params.set('city', $('#costCity').value.trim());
    params.set('hours', $('#costHours').value || 0);
    params.set('job_search_months', $('#costJobSearch').value || 0);

    msg.textContent = 'Calculating…';
    try {
      const r = await api('/costs/estimate/?' + params);
      msg.textContent = '';
      const p = r.part_time;
      const f = r.funding;
      const rows = r.breakdown.map(i => `<tr>
          <td>${esc(i.label)}<br><span class="muted small">${esc(i.note)}</span></td>
          <td>${basisBadge(i.basis)}</td>
          <td class="num">${i.local_amount === null ? '' : esc(r.currency + ' ' + i.local_amount.toLocaleString('en-US'))}</td>
          <td class="num">${fmtBDT(i.bdt)}</td></tr>`).join('');

      out.innerHTML = `
        <div class="cost-hero">
          <div class="card cost-box"><small>Total cost, whole program</small><b>${fmtBDT(r.total_bdt)}</b><span>${r.duration_months} months · ${fmtBDT(r.per_year_bdt)} per year</span></div>
          <div class="card cost-box"><small>Part-time job could recover</small><b>${fmtBDT(p.net_bdt)}</b><span>${p.covers_total_percent}% of total · ${p.covers_living_percent}% of living costs</span></div>
          <div class="card cost-box"><small>You still need to fund</small><b>${fmtBDT(r.net_after_part_time_bdt)}</b><span>after part-time income</span></div>
          ${f ? `<div class="card cost-box ${f.status === 'shortfall' ? 'bad' : 'good'}"><small>${f.status === 'shortfall' ? 'Shortfall against your budget' : 'Within your budget'}</small>
            <b>${fmtBDT(f.status === 'shortfall' ? f.gap_bdt : f.surplus_bdt)}</b><span>Budget ${fmtBDT(f.budget_for_whole_program_bdt)} for the program</span></div>`
            : '<div class="card cost-box"><small>Your budget</small><b>–</b><span>Save your profile to compare</span></div>'}
        </div>
        ${r.warnings.length ? `<div class="warn">${r.warnings.map(w => `<div>⚠ ${esc(w)}</div>`).join('')}</div>` : ''}
        <div class="card panel-pad">
          <div class="panel-head"><h2>Cost breakdown${r.opportunity ? ' — ' + esc(r.opportunity) : ' — ' + esc(COUNTRY_NAMES[r.country] || r.country)}</h2></div>
          <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Item</th><th>Source</th><th class="num">${esc(r.currency)}</th><th class="num">BDT</th></tr></thead>
            <tbody>${rows}</tbody>
            <tfoot><tr><td colspan="2">Total</td><td class="num">${esc(r.currency)} ${r.total_local.toLocaleString('en-US')}</td><td class="num">${fmtBDT(r.total_bdt)}</td></tr></tfoot></table></div>
          <p class="muted small">Exchange rate: 1 ${esc(r.currency)} = ৳${r.exchange_rate.bdt_per_unit} (Bangladesh Bank, ${esc(fmtDate(r.exchange_rate.as_of))}). Rates move daily.</p>
        </div>
        <div class="card panel-pad">
          <div class="panel-head"><h2>Part-time work</h2>${basisBadge(p.basis.rules)}</div>
          <p class="small">${esc(p.hours_per_week)} h/week (legal maximum ${p.max_hours_per_week}) × ${esc(r.currency)} ${p.hourly_wage_local} per hour × ${p.working_weeks} working weeks
            (after ${p.job_search_months} months looking for a job) = <b>${esc(r.currency)} ${p.net_local.toLocaleString('en-US')}</b> after tax, about <b>${fmtBDT(p.net_bdt)}</b>.</p>
          <p class="muted small">${esc(p.note)}</p>
          <p class="muted small">This is an estimate, not a promise. Jobs can take time to find, and you must never exceed the legal hour limit.</p>
        </div>
        <div class="card panel-pad">
          <div class="panel-head"><h2>Visa proof of funds (first year)</h2>${basisBadge(r.proof_of_funds.basis)}</div>
          <p class="small">Living funds ${esc(r.currency)} ${r.proof_of_funds.living_local.toLocaleString('en-US')}
            ${r.proof_of_funds.first_year_tuition_local ? ' + first-year tuition ' + esc(r.currency) + ' ' + r.proof_of_funds.first_year_tuition_local.toLocaleString('en-US') : ''}
            = <b>${fmtBDT(r.proof_of_funds.total_bdt)}</b></p>
          <p class="muted small">${esc(r.proof_of_funds.note)}</p>
        </div>
        <div class="card panel-pad">
          <div class="panel-head"><h2>Sources</h2><span class="tag gold">Checked ${esc(fmtDate(r.verified_on))}</span></div>
          <ul class="src-list">${r.sources.map(s => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a></li>`).join('')}</ul>
          ${r.assumed_items.length ? `<p class="muted small">Assumptions to check: ${esc(r.assumed_items.join(', '))}.</p>` : ''}
          <p class="muted small">${esc(r.disclaimer)}</p>
        </div>`;
    } catch (error) {
      msg.textContent = error.message;
      out.innerHTML = '';
    }
  }
  $('#estimateButton').addEventListener('click', runEstimate);

  // ============================================================== DOCUMENTS
  $('#docType').addEventListener('change', () => {
    $('#docStyleField').hidden = $('#docType').value !== 'cv';
  });

  $('#docReviewBtn').addEventListener('click', async () => {
    const msg = $('#docMsg');
    const out = $('#docResult');
    const file = $('#docFile').files[0];
    const text = $('#docText').value.trim();
    if (!file && text.length < 100) { msg.textContent = 'Upload a file or paste at least 100 characters.'; return; }

    const data = new FormData();
    data.append('doc_type', $('#docType').value);
    data.append('style', $('#docStyle').value);
    if (file) data.append('file', file); else data.append('text', text);

    $('#docReviewBtn').disabled = true;
    msg.textContent = 'Reviewing…';
    try {
      const r = await api('/documents/review/', { method: 'POST', form: data });
      msg.textContent = '';
      const color = r.score >= 75 ? 'var(--green)' : r.score >= 50 ? 'var(--gold)' : 'var(--red)';
      const icon = { ok: '✓', warn: '!', missing: '✕' };
      out.innerHTML = `<div class="card panel-pad">
        <div style="display:flex;gap:18px;align-items:center;margin-bottom:12px">
          <div class="score-ring" style="background:conic-gradient(${color} 0 ${r.score}%,#e7edf3 ${r.score}% 100%)"><span>${r.score}</span></div>
          <div><h2 style="font:800 15px Manrope;color:var(--navy);margin:0">${r.doc_type === 'cv' ? 'CV review' : 'SOP review'}</h2>
            <p class="muted small" style="margin:6px 0 0">${r.word_count} words · a checklist of suggestions, not a grade from a university.</p></div></div>
        ${r.checks.map(c => `<div class="check-item"><span class="check-ico ${c.status}">${icon[c.status]}</span><span>${esc(c.message)}</span></div>`).join('')}
      </div>`;
    } catch (error) {
      msg.textContent = error.message;
    } finally {
      $('#docReviewBtn').disabled = false;
    }
  });

  // ========================================================== APPLICATIONS
  async function patchApp(id, body) {
    try { return await api(`/applications/${id}/`, { method: 'PATCH', body }); }
    catch (error) { toast(error.message); throw error; }
  }

  function appCard(a) {
    const o = a.opportunity_detail;
    const pct = a.checklist_progress.total ? Math.round(100 * a.checklist_progress.done / a.checklist_progress.total) : 0;
    const statusOptions = Object.entries(STATUS_LABELS)
      .map(([v, l]) => `<option value="${v}" ${v === a.status ? 'selected' : ''}>${l}</option>`).join('');
    const items = a.checklist.map(i => `<label class="todo ${i.done ? 'done' : ''}">
        <input type="checkbox" data-key="${esc(i.key)}" ${i.done ? 'checked' : ''}><span>${esc(i.label)}</span></label>`).join('');

    return `<article class="card app" data-id="${a.id}">
      <div class="app-head">
        <div class="prog-flag">${FLAGS[o.country] || '🌍'}</div>
        <div><h3>${esc(o.title)}</h3><div class="prog-sub">${esc(o.institution)} · ${esc(COUNTRY_NAMES[o.country] || o.country)}</div></div>
        <a class="link" style="margin-left:auto;font-size:11px;font-weight:800;color:#9a742f" href="${esc(o.source_url)}" target="_blank" rel="noopener noreferrer">Official page ↗</a>
      </div>
      <div class="app-ctrl">
        <div class="field"><label>Status</label><select data-field="status">${statusOptions}</select></div>
        <div class="field"><label>My deadline ${o.deadline ? '(official: ' + esc(fmtDate(o.deadline)) + ')' : ''}</label>
          <input type="date" data-field="deadline_override" value="${esc(a.deadline_override || '')}"></div>
      </div>
      <div class="muted small"><b>Documents ${a.checklist_progress.done}/${a.checklist_progress.total}</b> (general list; confirm on the official page)</div>
      <div class="bar"><div style="width:${pct}%"></div></div>
      <details class="app-more"><summary>Open checklist</summary>${items}</details>
      <div class="field" style="margin-top:14px"><label>Notes</label><textarea rows="2" data-field="notes" maxlength="5000" placeholder="Login details location, contact person, reminders…">${esc(a.notes)}</textarea></div>
      <div class="prog-actions"><button class="btn btn-outline btn-sm danger" data-act="delete">Remove from tracker</button></div>
    </article>`;
  }

  async function loadApplications() {
    const list = $('#appList');
    try {
      const apps = await api('/applications/');
      state.loaded.apps = true;
      state.apps = apps;
      const counts = {};
      apps.forEach(a => { counts[a.status] = (counts[a.status] || 0) + 1; });
      $('#appSummary').innerHTML = apps.length
        ? Object.entries(STATUS_LABELS).filter(([k]) => counts[k])
            .map(([k, l]) => `<div class="sum-pill"><b>${counts[k]}</b>${l}</div>`).join('')
        : '';
      list.innerHTML = apps.length ? apps.map(appCard).join('')
        : '<div class="card empty">Nothing here yet. Open “Find programs” and press “Add to tracker”.</div>';
    } catch (error) {
      list.innerHTML = `<div class="card empty">${esc(error.message)}</div>`;
    }
  }

  const appList = $('#appList');
  appList.addEventListener('change', async event => {
    const card = event.target.closest('.app');
    if (!card) return;
    const id = card.dataset.id;

    if (event.target.matches('input[type=checkbox][data-key]')) {
      const app = state.apps.find(a => String(a.id) === id);
      const checklist = app.checklist.map(i => ({ ...i, done: i.key === event.target.dataset.key ? event.target.checked : i.done }));
      const saved = await patchApp(id, { checklist });
      Object.assign(app, saved);
      card.outerHTML = appCard(app).replace('<details class="app-more">', '<details class="app-more" open>');
    } else if (event.target.matches('[data-field]')) {
      const field = event.target.dataset.field;
      const value = event.target.value || (field === 'deadline_override' ? null : '');
      const saved = await patchApp(id, { [field]: value });
      Object.assign(state.apps.find(a => String(a.id) === id), saved);
      toast('Saved.');
      refreshOverview();
    }
  });

  appList.addEventListener('click', async event => {
    const button = event.target.closest('button[data-act="delete"]');
    if (!button) return;
    if (!confirm('Remove this program from your tracker?')) return;
    const card = button.closest('.app');
    try {
      await api(`/applications/${card.dataset.id}/`, { method: 'DELETE' });
      toast('Removed.');
      loadApplications();
      refreshOverview();
    } catch (error) { toast(error.message); }
  });

  // ============================================================== OVERVIEW
  async function refreshOverview() {
    const profile = await loadProfile().catch(() => null);
    const pct = profile ? profile.profile_completion : 0;
    $('#statProfile').textContent = pct + '%';
    $('#statProfileFoot').textContent = profile ? (pct === 100 ? 'Profile complete' : 'A few details still needed') : 'Not started';
    $('#overviewCta').innerHTML = profile ? 'Edit my profile <span>→</span>' : 'Complete my profile <span>→</span>';

    api('/opportunities/?type=program').then(p => { $('#statPrograms').textContent = p.length; }).catch(() => {});

    api('/applications/').then(apps => {
      $('#statApps').textContent = apps.length;
      const upcoming = apps
        .map(a => ({ a, d: a.effective_deadline }))
        .filter(x => x.d && new Date(x.d) >= new Date(new Date().toDateString()))
        .sort((x, y) => x.d.localeCompare(y.d))[0];
      $('#statDeadline').textContent = upcoming ? fmtDate(upcoming.d) : 'None set';
      $('#statDeadlineFoot').textContent = upcoming ? upcoming.a.opportunity_detail.title : 'Add deadlines in your tracker';
      renderSteps(profile, apps);
    }).catch(() => renderSteps(profile, []));

    const box = $('#overviewMatches');
    if (!profile) { box.innerHTML = '<p class="muted small">Save your profile to see your best matches.</p>'; return; }
    api('/opportunities/recommendations/?limit=3').then(r => {
      box.innerHTML = r.results.length ? r.results.map(m => `<div class="program-row">
          <div class="uni-mark">${FLAGS[m.country] || '🌍'}</div>
          <div><strong>${esc(m.title)}</strong><small>${esc(m.institution)} · ${esc(m.label)}</small></div>
          <div class="program-meta"><strong>${m.score}</strong><small>of 100</small></div></div>`).join('')
        : '<p class="muted small">No programs stored yet.</p>';
    }).catch(() => { box.innerHTML = '<p class="muted small">Matches are not available right now.</p>'; });
  }

  function renderSteps(profile, apps) {
    const steps = [];
    if (!profile) steps.push(['Build your profile', 'Upload your CV or fill the form', 'profile']);
    else {
      if (!profile.english_test || !profile.overall_score) steps.push(['Add your English test result', 'IELTS, TOEFL or PTE', 'profile']);
      if (!profile.destination_preferences.length) steps.push(['Choose your destinations', 'This improves your matches', 'profile']);
      if (!profile.target_field && !(profile.skills || []).length) steps.push(['Add your target field and skills', 'Helps rank programs', 'profile']);
    }
    if (!apps.length) steps.push(['Shortlist 3 to 5 programs', 'Compare requirements and costs', 'programs']);
    steps.push(['Check the full cost of your top choice', 'Include visa funds and part-time income', 'costs']);
    steps.push(['Review your CV and SOP', 'Fix gaps before you apply', 'documents']);
    $('#overviewSteps').innerHTML = steps.slice(0, 5).map(([t, s, go]) => `
      <div class="task-row"><span class="task-check"></span><div><strong>${esc(t)}</strong><small>${esc(s)}</small></div>
      <a class="task-date" href="#${go}" data-go="${go}">Open</a></div>`).join('');
    $$('#overviewSteps [data-go]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); window.PW.activate(a.dataset.go); }));
  }

  // ============================================================ NAVIGATION
  document.addEventListener('pw:section', async event => {
    const id = event.detail;
    if (id === 'overview') refreshOverview();
    if (id === 'profile') loadProfile().catch(e => showMessage(e.message));
    if (id === 'programs' && !state.loaded.programs) { state.loaded.programs = true; loadPrograms(); }
    if (id === 'scholarships' && !state.loaded.scholarships) { state.loaded.scholarships = true; loadScholarships(); }
    if (id === 'costs') fillCostPrograms();
    if (id === 'applications') loadApplications();
  });

  // first load
  loadProfile().catch(() => {}).then(refreshOverview);
})();
