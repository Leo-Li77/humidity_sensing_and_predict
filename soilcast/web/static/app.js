/* ==========================================================================
   Console logic: polling, view switching, rendering.
   ========================================================================== */

(() => {
  'use strict';

  const ICONS = {
    grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/></svg>',
    drop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 3c3.4 4 6 7.2 6 10.2A6 6 0 0 1 6 13.2C6 10.2 8.6 7 12 3z"/></svg>',
    table: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/></svg>',
    chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 19V5M4 19h16"/><path d="M8 15l3.5-4 3 2.5L20 7"/></svg>',
    brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.6V16a3 3 0 0 0 3 3h1V4z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.6V16a3 3 0 0 1-3 3h-1V4z"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
  };

  const TITLES = {
    dashboard: ['Overview', 'Live readings from the Raspberry Pi'],
    collect: ['Collect', 'Start a run, watch it arrive, stop it when you have enough'],
    data: ['Data', 'Every reading held in data/raw.csv'],
    analysis: ['Analysis', 'Descriptive statistics and diagnostics computed in the browser'],
    model: ['Model', 'Fit the baseline and the LSTM, then compare them'],
    history: ['History', 'What has been run on this device, and how it went'],
  };

  const state = {
    view: 'dashboard',
    status: null,
    rows: [],
    page: 0,
    pageSize: 25,
    collectLogSeen: new Set(),
    livePoints: [],
    lastRowCount: -1,
    rendered: { dashboard: -1, data: -1, analysis: -1 },
    driverPicked: false,
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  /* ------------------------------------------------------------------ api */
  async function api(path, options) {
    const response = await fetch(path, Object.assign({ headers: {} }, options));
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (error) { data = { raw: text }; }
    if (!response.ok) {
      const message = (data && (data.error || data.message)) || response.statusText;
      throw new Error(message);
    }
    return data;
  }

  function toast(message, isError) {
    const el = $('#toast');
    el.textContent = message;
    el.className = 'toast is-shown' + (isError ? ' is-error' : '');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { el.className = 'toast'; }, 3400);
  }

  /* --------------------------------------------------------------- helpers */
  function parseStamp(text) {
    return new Date(String(text).replace(' ', 'T'));
  }

  function mean(values) {
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : NaN;
  }

  function stdDev(values) {
    if (values.length < 2) return NaN;
    const m = mean(values);
    return Math.sqrt(values.reduce((acc, v) => acc + (v - m) ** 2, 0) / values.length);
  }

  function histogram(values, bins) {
    if (!values.length) return { counts: [], edges: [] };
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const counts = new Array(bins).fill(0);
    values.forEach((v) => {
      const index = Math.min(bins - 1, Math.floor((v - min) / span * bins));
      counts[index] += 1;
    });
    const edges = [];
    for (let i = 0; i <= bins; i += 1) edges.push(min + (span * i) / bins);
    return { counts, edges };
  }

  function autocorrelation(values, nlags) {
    const n = values.length;
    if (n < nlags + 3) return [];
    const m = mean(values);
    const centred = values.map((v) => v - m);
    const denominator = centred.reduce((acc, v) => acc + v * v, 0) || 1;
    const out = [];
    for (let lag = 0; lag <= nlags; lag += 1) {
      let sum = 0;
      for (let i = 0; i + lag < n; i += 1) sum += centred[i] * centred[i + lag];
      out.push(sum / denominator);
    }
    return out;
  }

  /* ----------------------------------------------------------------- icons */
  function paintIcons() {
    $$('[data-icon]').forEach((node) => {
      node.innerHTML = ICONS[node.dataset.icon] || '';
    });
  }

  /* ------------------------------------------------------------------ view */
  function go(view) {
    state.view = view;
    state.rendered[view] = -1;
    $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.go === view));
    $$('.view').forEach((section) => {
      section.classList.toggle('is-active', section.dataset.view === view);
    });
    const [title, sub] = TITLES[view] || ['', ''];
    $('#viewTitle').textContent = title;
    $('#viewSub').textContent = sub;
    render();
  }

  /* --------------------------------------------------------------- polling */
  function setLink(live) {
    const link = $('#linkState');
    link.className = 'link-state ' + (live ? 'is-live' : 'is-down');
    $('#linkText').textContent = live ? 'live' : 'offline';
  }

  async function poll() {
    let status;
    try {
      status = await api('/api/status');
    } catch (error) {
      setLink(false);
      return;
    }
    state.status = status;
    setLink(true);
    $('#clockPill').textContent = new Date().toLocaleTimeString();

    if (status.rows !== state.lastRowCount) {
      state.lastRowCount = status.rows;
      loadRows();
    }
    try {
      render();
    } catch (error) {
      // A drawing error must not be reported as a lost connection.
      console.error(error);
    }
  }

  /* ---------------------------------------------------------------- render */
  function render() {
    const status = state.status;
    if (!status) return;

    $('#driverPill').textContent = 'driver \u00b7 ' + (status.config.driver || '\u2014');

    const rows = state.rows;
    if (state.view === 'dashboard' && rows.length !== state.rendered.dashboard) {
      renderDashboard(status, rows);
    }
    if (state.view === 'collect') renderCollect(status);
    if (state.view === 'data' && rows.length !== state.rendered.data) renderData();
    if (state.view === 'analysis' && rows.length !== state.rendered.analysis) renderAnalysis();
  }

  async function loadRows() {
    try {
      const data = await api('/api/readings');
      state.rows = data.rows || [];
      // Force every view to redraw against the new rows.
      state.rendered = { dashboard: -1, data: -1, analysis: -1 };
      render();
    } catch (error) { /* the next poll will try again */ }
  }

  /* ------------------------------------------------------------- dashboard */
  function renderDashboard(status, rows) {
    const latest = rows[rows.length - 1];
    const summary = status.summary || {};

    $('#mMoisture').textContent = latest ? latest.moisture.toFixed(1) : '\u2014';
    $('#mTemp').textContent = latest ? latest.temperature.toFixed(1) : '\u2014';
    $('#mHumidity').textContent = latest ? latest.humidity.toFixed(1) : '\u2014';
    $('#mRows').textContent = rows.length;

    $('#mMoistureNote').textContent = latest
      ? 'at ' + latest.timestamp.slice(11, 16)
      : 'no reading yet';
    $('#mTempNote').textContent = status.config.ambient_source === 'dataset'
      ? 'from the public data set' : 'from the ambient source';
    $('#mHumidityNote').textContent = summary.rows
      ? `mean ${summary.humidity.mean.toFixed(1)} %`
      : 'ambient';
    $('#mRowsNote').textContent = summary.first
      ? `${summary.first.slice(0, 10)} to ${summary.last.slice(0, 10)}`
      : 'since the last clear';

    Charts.gauge($('#gaugeMoisture'), {
      value: latest ? latest.moisture : 0,
      max: 100,
      threshold: status.config.watering_threshold,
      warn: status.config.watering_threshold + 20,
    });

    if (latest) {
      const low = latest.moisture < status.config.watering_threshold;
      $('#gaugeFoot').textContent = low
        ? `Below the ${status.config.watering_threshold} % threshold. This pot needs water.`
        : `Above the ${status.config.watering_threshold} % threshold. No watering needed yet.`;
    } else {
      $('#gaugeFoot').textContent = 'Waiting for the first reading.';
    }

    const points = rows.map((r) => ({ t: parseStamp(r.timestamp).getTime(), y: r.moisture }));
    Charts.line($('#chartSeries'), {
      series: [{ name: 'Moisture', color: '#6E1B2B', points, area: true, endLabel: true }],
      threshold: { value: status.config.watering_threshold, label: 'watering threshold' },
      emptyText: 'Collect or upload some readings to see the curve.',
    });

    $('#seriesLegend').innerHTML = `
      <span><i class="swatch" style="background:#6E1B2B"></i>soil moisture</span>
      <span><i class="swatch" style="background:#A3242A;opacity:.6"></i>threshold</span>`;

    Charts.line($('#chartAmbient'), {
      series: [
        { name: 'Temperature', color: '#A9641B', points: rows.map((r) => ({ t: parseStamp(r.timestamp).getTime(), y: r.temperature })) },
        { name: 'Humidity', color: '#2E6B4F', points: rows.map((r) => ({ t: parseStamp(r.timestamp).getTime(), y: r.humidity })) },
      ],
      emptyText: 'No ambient readings yet.',
    });

    state.rendered.dashboard = rows.length;
  }

  /* --------------------------------------------------------------- collect */
  function renderCollect(status) {
    // On a laptop with no ADC, starting a collection with the ADS1115 driver
    // would only fail. Pick the simulator once, and leave the choice to the user
    // afterwards.
    if (!state.driverPicked && status.hardware) {
      state.driverPicked = true;
      if (!status.hardware.ads1115 && $('#driverSelect').value === 'ads1115') {
        $('#driverSelect').value = 'simulate';
      }
      updateDriverHint();
    }

    const collect = status.collect || {};
    const running = collect.running;
    $('#startBtn').disabled = running;
    $('#stopBtn').disabled = !running;

    const written = collect.written || 0;
    const total = collect.total || 0;
    $('#collectCount').textContent = running || written
      ? `${written} / ${total}`
      : 'idle';
    $('#collectLabel').textContent = running
      ? collect.label || 'collecting'
      : (written ? `last run wrote ${written} samples` : 'Nothing is running.');

    Charts.ring($('#collectRing'), {
      value: total ? written / total : 0,
      color: running ? '#6E1B2B' : '#9A8D89',
    });

    if (collect.last && !state.collectLogSeen.has(collect.last.timestamp)) {
      state.collectLogSeen.add(collect.last.timestamp);
      state.livePoints.push({
        t: parseStamp(collect.last.timestamp).getTime(),
        y: collect.last.moisture,
      });
      if (state.livePoints.length > 240) state.livePoints.shift();
      const log = $('#collectLog');
      if (log.querySelector('.subtle')) log.innerHTML = '';
      log.innerHTML += `<div>${collect.last.timestamp}  moisture ${collect.last.moisture.toFixed(1)} %  `
        + `${collect.last.temperature.toFixed(1)} C  ${collect.last.humidity.toFixed(1)} %</div>`;
      log.scrollTop = log.scrollHeight;
    }

    if (collect.running && !collect.last) {
      $('#collectLog').innerHTML = '<span class="subtle">Waiting for the first sample.</span>';
    }

    const live = state.livePoints.length
      ? state.livePoints
      : state.rows.slice(-120).map((r) => ({ t: parseStamp(r.timestamp).getTime(), y: r.moisture }));

    Charts.line($('#chartLive'), {
      series: [{ name: 'Moisture', color: '#6E1B2B', points: live, area: true, dots: true }],
      threshold: { value: status.config.watering_threshold, label: 'threshold' },
      emptyText: 'Start a collection to see samples arrive here.',
    });
  }

  /* ------------------------------------------------------------------ data */
  function renderData() {
    const rows = state.rows;
    const summary = (state.status && state.status.summary) || {};

    $('#dRows').textContent = rows.length;
    $('#dSpan').textContent = summary.first
      ? `${summary.first.slice(0, 10)} to ${summary.last.slice(0, 10)}`
      : 'no data yet';

    if (rows.length) {
      const values = rows.map((r) => r.moisture);
      const min = Math.min(...values);
      const max = Math.max(...values);
      $('#dRange').textContent = `${min.toFixed(1)} \u2013 ${max.toFixed(1)}`;
      $('#dMean').textContent = mean(values).toFixed(1);
      $('#dStd').textContent = `standard deviation ${stdDev(values).toFixed(2)}`;
    } else {
      $('#dRange').textContent = '\u2014';
      $('#dMean').textContent = '\u2014';
      $('#dStd').textContent = 'standard deviation \u2014';
    }

    const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    state.page = Math.min(state.page, totalPages - 1);
    const newestFirst = rows.slice().reverse();
    const slice = newestFirst.slice(
      state.page * state.pageSize, (state.page + 1) * state.pageSize
    );

    const body = $('#dataTable tbody');
    if (!slice.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">No rows yet.</td></tr>';
    } else {
      body.innerHTML = slice.map((r, i) => `
        <tr>
          <td>${state.page * state.pageSize + i + 1}</td>
          <td>${r.timestamp}</td>
          <td>${r.moisture !== null ? r.moisture.toFixed(2) : '\u2014'}</td>
          <td>${r.temperature !== null ? r.temperature.toFixed(1) : '\u2014'}</td>
          <td>${r.humidity !== null ? r.humidity.toFixed(1) : '\u2014'}</td>
        </tr>`).join('');
    }
    $('#pageInfo').textContent = `page ${state.page + 1} of ${totalPages}`;
    $('#pagePrev').disabled = state.page === 0;
    $('#pageNext').disabled = state.page >= totalPages - 1;
    state.rendered.data = rows.length;
  }

  /* -------------------------------------------------------------- analysis */
  function renderAnalysis() {
    const rows = state.rows;
    const values = rows.map((r) => r.moisture);
    const n = values.length;

    $('#sCount').textContent = n;
    $('#sMean').textContent = n ? mean(values).toFixed(1) : '\u2014';
    $('#sStd').textContent = n ? stdDev(values).toFixed(2) : '\u2014';
    $('#sMin').textContent = n ? Math.min(...values).toFixed(1) : '\u2014';

    if (!n) {
      Charts.bars($('#chartHist'), { values: [] });
      Charts.bars($('#chartAcf'), { values: [] });
      $('#statGrid').innerHTML = '<div class="stat"><dt>Samples</dt><dd>0</dd></div>';
      return;
    }

    const { counts, edges } = histogram(values, 14);
    Charts.bars($('#chartHist'), {
      values: counts,
      labels: edges.slice(0, -1).map((e) => e.toFixed(0)),
      color: '#6E1B2B',
    });

    const step = 1;
    const differenced = values.slice(step).map((v, i) => v - values[i]);
    const acf = autocorrelation(differenced, Math.min(48, Math.floor(differenced.length / 3)));
    // Drawn as a line rather than bars: three quarters of the values are negative
    // and a bar chart from zero would hide the ones that matter.
    Charts.line($('#chartAcf'), {
      series: [{
        name: 'ACF',
        color: '#2E6B4F',
        width: 2,
        dots: acf.length <= 50,
        points: acf.map((v, i) => ({ t: i, y: v })),
      }],
      xLabel: (t) => `${Math.round(t * 0.5)}h`,
      yMin: Math.min(-0.2, Math.min(...acf)),
      yMax: 1.05,
    });

    const stat = (label, value, unit) => `
      <div class="stat"><dt>${label}</dt><dd>${value}${unit ? ` <small>${unit}</small>` : ''}</dd></div>`;

    $('#statGrid').innerHTML = [
      stat('Samples', n),
      stat('Mean', mean(values).toFixed(2), '%'),
      stat('Median', values.slice().sort((a, b) => a - b)[Math.floor(n / 2)].toFixed(2), '%'),
      stat('Std deviation', stdDev(values).toFixed(2), '%'),
      stat('Minimum', Math.min(...values).toFixed(2), '%'),
      stat('Maximum', Math.max(...values).toFixed(2), '%'),
      stat('Range', (Math.max(...values) - Math.min(...values)).toFixed(2), '%'),
      stat('First reading', rows[0].timestamp.slice(0, 16)),
    ].join('');

    state.rendered.analysis = n;
  }

  /* ----------------------------------------------------------------- model */
  async function renderModel() {
    let data = {};
    try { data = await api('/api/metrics'); } catch (error) { data = {}; }
    const results = data.results || {};
    const linear = results.linear || {};
    const lstm = results.lstm || {};

    $('#kLinMae').textContent = linear.mae !== undefined ? linear.mae.toFixed(3) : '\u2014';
    $('#kLstmMae').textContent = lstm.mae !== undefined ? lstm.mae.toFixed(3) : '\u2014';
    $('#kLinR2').textContent = linear.r2 !== undefined ? `R\u00b2 ${linear.r2.toFixed(3)}` : 'R\u00b2 \u2014';
    $('#kLstmR2').textContent = lstm.r2 !== undefined ? `R\u00b2 ${lstm.r2.toFixed(3)}` : 'R\u00b2 \u2014';
    $('#kHorizon').textContent = data.lead_time_hours !== undefined ? data.lead_time_hours : '\u2014';
    $('#kTest').textContent = data.test_rows !== undefined ? data.test_rows : '\u2014';
    $('#kParams').textContent = lstm.n !== undefined
      ? `evaluated on ${lstm.n} rows` : (linear.n !== undefined ? `evaluated on ${linear.n} rows` : 'parameters \u2014');

    const bars = [];
    const labels = [];
    ['mae', 'rmse'].forEach((metric) => {
      if (linear[metric] !== undefined) { bars.push(linear[metric]); labels.push(`Ridge ${metric.toUpperCase()}`); }
      if (lstm[metric] !== undefined) { bars.push(lstm[metric]); labels.push(`LSTM ${metric.toUpperCase()}`); }
    });
    Charts.bars($('#chartMetrics'), {
      values: bars,
      labels,
      color: '#6E1B2B',
    });

    const rows = [];
    const push = (label, value) => rows.push(
      `<div class="stat"><dt>${label}</dt><dd>${value}</dd></div>`);

    if (data.rows) push('Rows with a target', data.rows);
    if (data.train_rows) push('Training rows', data.train_rows);
    if (data.test_rows) push('Test rows', data.test_rows);
    if (data.gap_rows !== undefined) push('Discarded gap', `${data.gap_rows} rows`);
    if (lstm.r2 !== undefined) {
      push('LSTM R\u00b2', lstm.r2 >= 0.9 ? 'strong fit' : lstm.r2 >= 0.5 ? 'moderate' : 'weak');
    }
    if (data.lstm_residual_band) {
      push('80 % residual band', `${data.lstm_residual_band.low.toFixed(2)} to ${data.lstm_residual_band.high.toFixed(2)} %`);
    }
    if (data.sensor_driver) push('Sensor used', data.sensor_driver);
    if (data.ambient_source) push('Ambient source', data.ambient_source);
    $('#metricGrid').innerHTML = rows.length ? rows.join('')
      : '<div class="stat"><dt>Nothing trained</dt><dd>Run training first</dd></div>';
  }

  /* --------------------------------------------------------------- history */
  async function renderHistory() {
    const filter = $('#historyFilter').value;
    let runs = [];
    try {
      const data = await api('/api/history' + (filter ? `?kind=${filter}` : ''));
      runs = data.runs || [];
    } catch (error) { runs = []; }

    const body = $('#historyTable tbody');
    if (!runs.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">Nothing recorded yet.</td></tr>';
      return;
    }

    const detail = (run) => {
      const p = run.payload || {};
      if (run.kind === 'collect') return `${run.samples || 0} samples, driver ${run.driver || '\u2014'}`;
      if (run.kind === 'train') {
        const lin = (p.results && p.results.linear) || {};
        const lstm = (p.results && p.results.lstm) || {};
        const parts = [];
        if (lin.mae !== undefined) parts.push(`ridge MAE ${lin.mae.toFixed(2)}`);
        if (lstm.mae !== undefined) parts.push(`LSTM MAE ${lstm.mae.toFixed(2)}`);
        return parts.join(', ') || 'trained';
      }
      if (run.kind === 'import') return `${run.samples || 0} rows from ${p.file || 'a file'}`;
      if (p.rows !== undefined) return `${p.rows} rows`;
      return '\u2014';
    };

    body.innerHTML = runs.map((run) => `
      <tr>
        <td>${(run.started_at || '').replace('T', ' ').slice(0, 16)}</td>
        <td>${run.kind}</td>
        <td>${detail(run)}</td>
        <td>${run.ok ? '<span style="color:#2E6B4F">ok</span>' : '<span style="color:#A3242A">failed</span>'}</td>
        <td><button class="btn btn-ghost" style="padding:5px 11px;font-size:12px"
          data-del="${run.id}">Remove</button></td>
      </tr>`).join('');

    body.querySelectorAll('[data-del]').forEach((button) => {
      button.addEventListener('click', async () => {
        await api('/api/history/' + button.dataset.del, { method: 'DELETE' });
        renderHistory();
      });
    });
  }

  /* ---------------------------------------------------------------- events */
  function bind() {
    $$('.nav-item').forEach((button) => {
      button.addEventListener('click', () => {
        go(button.dataset.go);
        if (button.dataset.go === 'history') renderHistory();
        if (button.dataset.go === 'model') renderModel();
      });
    });

    $('#collectForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      state.collectLogSeen.clear();
      state.livePoints = [];
      $('#collectLog').innerHTML = '<span class="subtle">Starting\u2026</span>';
      try {
        const result = await api('/api/collect/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            driver: form.get('driver'),
            interval: Number(form.get('interval')),
            hours: Number(form.get('hours')),
            append: form.get('append') === 'on',
            speed: Number(form.get('speed')),
          }),
        });
        const clock = Number(form.get('speed'));
        toast(clock > 1 || clock === 0
          ? `Collection started: ${result.samples} samples, clock ${
            clock === 0 ? 'at full speed' : `${clock}\u00d7`}`
          : `Collection started: ${result.samples} samples`);
        poll();
      } catch (error) {
        toast(error.message, true);
      }
    });

    $('#stopBtn').addEventListener('click', async () => {
      try {
        await api('/api/collect/stop', { method: 'POST' });
        toast('Stopping after the current sample');
      } catch (error) { toast(error.message, true); }
    });

    const runJob = async (path, body, label) => {
      try {
        await api(path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body || {}),
        });
        toast(`${label} started`);
        watchJob(label);
      } catch (error) { toast(error.message, true); }
    };

    $('#preprocessBtn').addEventListener('click', () =>
      runJob('/api/preprocess', {}, 'Preprocessing'));
    $('#analyzeBtn').addEventListener('click', () =>
      runJob('/api/analyze', {}, 'Figure generation'));
    $('#trainBtn').addEventListener('click', () =>
      runJob('/api/train', {
        horizon: Number($('#horizonSelect').value),
        no_lstm: $('#noLstm').checked,
      }, 'Training'));

    $('#driverSelect').addEventListener('change', updateDriverHint);
    updateDriverHint();

    // Duration, interval and speed all change the estimate under the control.
    ['#speedSelect', '#collectForm [name=hours]', '#collectForm [name=interval]']
      .forEach((selector) => {
        const element = $(selector);
        if (element) element.addEventListener('change', updateSpeedHint);
      });

    $('#uploadInput').addEventListener('change', async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      const body = new FormData();
      body.append('file', file);
      try {
        const result = await api('/api/upload', { method: 'POST', body });
        toast(`Imported ${result.rows} rows`);
        state.lastRowCount = -1;
        poll();
      } catch (error) { toast(error.message, true); }
    });

    $('#clearBtn').addEventListener('click', async () => {
      if (!confirm('Move the current readings to a backup file and start empty?')) return;
      try {
        const result = await api('/api/clear', { method: 'POST' });
        toast(result.archived ? `Archived to ${result.archived}` : 'Nothing to clear');
        state.lastRowCount = -1;
        state.livePoints = [];
        poll();
      } catch (error) { toast(error.message, true); }
    });

    $('#pagePrev').addEventListener('click', () => {
      state.page = Math.max(0, state.page - 1);
      renderData();
    });
    $('#pageNext').addEventListener('click', () => {
      state.page += 1;
      renderData();
    });

    $('#historyFilter').addEventListener('change', renderHistory);
    $('#historyRefresh').addEventListener('click', renderHistory);
  }

  let lastDriver = null;

  function updateDriverHint() {
    const driver = $('#driverSelect').value;
    const hints = {
      ads1115: 'Reads the analog output through the ADS1115 on I2C. This is the only mode that produces a continuous 0 to 100 % curve.',
      simulate: 'No hardware. A physically plausible drying curve, useful for checking the pipeline end to end. This is the only mode whose clock can be wound forward.',
      gpio: 'Reads the digital output only, so every sample is 0 or 100 %. Use it to prove the wiring, not for experiments.',
    };
    $('#driverHint').textContent = hints[driver] || '';

    // The clock only means something for the simulator, so the control appears
    // and disappears with the driver rather than sitting there greyed out. The
    // speed is reset only when leaving the simulator, so that the value it was
    // given in the page keeps when the driver is picked automatically on load.
    const simulated = driver === 'simulate';
    if (lastDriver === 'simulate' && !simulated) $('#speedSelect').value = '1';
    lastDriver = driver;
    $('#speedField').hidden = !simulated;
    updateSpeedHint();
  }

  function updateSpeedHint() {
    const form = $('#collectForm');
    const hours = Number(form.querySelector('[name=hours]').value);
    const interval = Number(form.querySelector('[name=interval]').value);
    const speed = Number($('#speedSelect').value);
    const samples = Math.max(1, Math.round(hours * 60 / interval));
    const box = $('#speedHint');

    if (speed <= 0) {
      box.textContent = `${samples} samples written as fast as the disk allows; the timestamps keep their ${interval}-minute spacing.`;
    } else if (speed === 1) {
      box.textContent = `${samples} samples over ${hours} h of real time. Pick a speed to watch it without waiting.`;
    } else {
      const minutes = (hours * 60) / speed;
      const human = minutes < 1
        ? 'under a minute'
        : minutes < 90
          ? `about ${Math.round(minutes)} minutes`
          : `about ${(minutes / 60).toFixed(1)} hours`;
      box.textContent = `${hours} h of readings, ${samples} samples, written in ${human}.`;
    }
  }

  async function watchJob(label) {
    const started = Date.now();
    const tick = async () => {
      try {
        const job = await api('/api/job');
        const box = label === 'Training' ? $('#trainLog') : $('#analysisLog');
        if (job.log) box.textContent = job.log;
        if (job.running) {
          setTimeout(tick, 900);
          return;
        }
        box.textContent = job.log || box.textContent;
        toast(job.ok ? `${label} finished` : `${label} failed`, !job.ok);
        if (job.ok) {
          if (label === 'Training') renderModel();
          state.lastRowCount = -1;
          poll();
        }
      } catch (error) {
        if (Date.now() - started < 120000) setTimeout(tick, 1200);
      }
    };
    setTimeout(tick, 700);
  }

  /* ------------------------------------------------------------------ boot */
  async function boot() {
    paintIcons();
    bind();
    await loadRows();
    // ?view=collect opens straight onto a tab, which is handy for a bookmark or
    // for showing one screen without clicking.
    const wanted = new URLSearchParams(location.search).get('view');
    go(TITLES[wanted] ? wanted : 'dashboard');
    if (wanted === 'history') renderHistory();
    if (wanted === 'model') renderModel();
    poll();
    setInterval(poll, 2000);
  }

  boot();
})();
