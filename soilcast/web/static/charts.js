/* ==========================================================================
   Minimal SVG charting. No libraries, no network, no canvas.
   Every chart re-draws on resize so text keeps its size.
   ========================================================================== */

const Charts = (() => {
  const hosts = new Set();

  window.addEventListener('resize', () => {
    hosts.forEach((host) => {
      if (host.isConnected && host._draw) host._draw();
    });
  });

  const NS = 'http://www.w3.org/2000/svg';

  function mount(host, draw) {
    host._draw = draw;
    hosts.add(host);
    draw();
  }

  function esc(text) {
    return String(text).replace(/[&<>"]/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
    ));
  }

  function num(value) {
    return Number.isFinite(value) ? value : 0;
  }

  /* Pick round tick values that cover the range with about `count` steps. */
  function ticks(min, max, count) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (min === max) {
      const pad = Math.abs(min) * 0.1 || 1;
      min -= pad;
      max += pad;
    }
    const raw = (max - min) / Math.max(1, count);
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const candidates = [1, 2, 2.5, 5, 10].map((m) => m * magnitude);
    const step = candidates.find((c) => c >= raw) || magnitude * 10;
    const start = Math.floor(min / step) * step;
    const end = Math.ceil(max / step) * step;
    const out = [];
    for (let v = start; v <= end + step * 0.5; v += step) {
      out.push(Math.round(v * 1e6) / 1e6);
    }
    return out;
  }

  function fmt(value, digits) {
    if (!Number.isFinite(value)) return '\u2014';
    const d = digits === undefined ? (Math.abs(value) >= 100 ? 0 : 1) : digits;
    return value.toFixed(d);
  }

  function empty(host, message) {
    host.innerHTML =
      `<div style="display:flex;height:100%;align-items:center;justify-content:center;
        color:#9A8D89;font-size:13.5px">${esc(message)}</div>`;
  }

  /* ------------------------------------------------------------------ line */
  function line(host, config) {
    const draw = () => {
      const w = host.clientWidth || 640;
      const h = host.clientHeight || 300;
      if (w < 40 || h < 40) return;

      const series = (config.series || []).filter((s) => s.points && s.points.length);
      if (!series.length) { empty(host, config.emptyText || 'No data yet.'); return; }

      const pad = { left: 52, right: 16, top: 18, bottom: 34 };
      const iw = Math.max(10, w - pad.left - pad.right);
      const ih = Math.max(10, h - pad.top - pad.bottom);

      let xs = [], ys = [];
      series.forEach((s) => s.points.forEach((p) => {
        xs.push(p.t); ys.push(num(p.y));
      }));
      if (config.threshold) ys.push(config.threshold.value);

      const xMin = Math.min(...xs), xMax = Math.max(...xs);
      const yTicks = ticks(
        config.yMin !== undefined ? config.yMin : Math.min(...ys),
        config.yMax !== undefined ? config.yMax : Math.max(...ys),
        4
      );
      const yMin = config.yMin !== undefined ? config.yMin : yTicks[0];
      const yMax = config.yMax !== undefined ? config.yMax : yTicks[yTicks.length - 1];

      const px = (t) => pad.left + (xMax === xMin ? iw / 2 : (t - xMin) / (xMax - xMin) * iw);
      const py = (v) => pad.top + ih - (yMax === yMin ? ih / 2 : (v - yMin) / (yMax - yMin) * ih);

      let svg = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"
        font-family="Inter, system-ui, sans-serif">`;

      /* grid and y labels */
      yTicks.forEach((v) => {
        const y = py(v);
        svg += `<line x1="${pad.left}" y1="${y}" x2="${w - pad.right}" y2="${y}"
          stroke="rgba(36,31,32,0.08)" stroke-width="1"/>`;
        svg += `<text x="${pad.left - 9}" y="${y + 4}" text-anchor="end"
          font-size="11" fill="#8A7C78">${fmt(v)}</text>`;
      });

      /* x labels */
      const stamp = (t) => {
        const d = new Date(t);
        const span = xMax - xMin;
        if (span > 3 * 24 * 3600e3) return `${d.getMonth() + 1}/${d.getDate()}`;
        if (span > 20 * 3600e3) return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}h`;
        return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
      };
      const label = config.xLabel || stamp;
      const steps = Math.min(6, Math.max(2, Math.floor(iw / 92)));
      // A tick every so often can land on the same calendar day twice when the
      // span is a few days. Dropping the repeat keeps the axis honest.
      let previousLabel = null;
      for (let i = 0; i <= steps; i += 1) {
        const t = xMin + (xMax - xMin) * (i / steps);
        const text = label(t);
        if (text === previousLabel) continue;
        previousLabel = text;
        svg += `<text x="${px(t)}" y="${h - 12}" text-anchor="middle"
          font-size="11" fill="#8A7C78">${text}</text>`;
      }

      /* threshold */
      if (config.threshold) {
        const y = py(config.threshold.value);
        svg += `<line x1="${pad.left}" y1="${y}" x2="${w - pad.right}" y2="${y}"
          stroke="${config.threshold.color || '#A3242A'}" stroke-width="1.2"
          stroke-dasharray="5 5" opacity="0.55"/>`;
        if (config.threshold.label) {
          svg += `<text x="${w - pad.right}" y="${y - 6}" text-anchor="end" font-size="10.5"
            fill="${config.threshold.color || '#A3242A'}" opacity="0.8">${esc(config.threshold.label)}</text>`;
        }
      }

      /* series */
      series.forEach((s) => {
        const pts = s.points.map((p) => [px(p.t), py(num(p.y))]);
        const path = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
        const id = `g${Math.random().toString(36).slice(2, 8)}`;

        if (s.area) {
          svg += `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${s.color}" stop-opacity="0.24"/>
            <stop offset="100%" stop-color="${s.color}" stop-opacity="0.01"/>
          </linearGradient></defs>`;
          const baseline = pad.top + ih;
          svg += `<path d="${path} L${pts[pts.length - 1][0].toFixed(1)},${baseline} L${pts[0][0].toFixed(1)},${baseline} Z"
            fill="url(#${id})" stroke="none"/>`;
        }

        svg += `<path d="${path}" fill="none" stroke="${s.color}"
          stroke-width="${s.width || 2.2}" stroke-linecap="round" stroke-linejoin="round"
          ${s.dash ? 'stroke-dasharray="6 5"' : ''} />`;

        if (s.dots && pts.length <= 60) {
          pts.forEach((p) => {
            svg += `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.1"
              fill="#fff" stroke="${s.color}" stroke-width="1.4"/>`;
          });
        }

        const last = pts[pts.length - 1];
        if (s.endLabel) {
          svg += `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4"
            fill="${s.color}" opacity="0.16"/>`;
          svg += `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.4"
            fill="${s.color}"/>`;
        }
      });

      /* axis lines */
      svg += `<line x1="${pad.left}" y1="${pad.top + ih}" x2="${w - pad.right}"
        y2="${pad.top + ih}" stroke="rgba(36,31,32,0.16)" stroke-width="1"/>`;

      svg += '</svg>';
      host.innerHTML = svg;
    };
    mount(host, draw);
  }

  /* ------------------------------------------------------------------ bars */
  function bars(host, config) {
    const draw = () => {
      const w = host.clientWidth || 520;
      const h = host.clientHeight || 300;
      const values = config.values || [];
      if (!values.length) { empty(host, 'No data yet.'); return; }

      const pad = { left: 46, right: 14, top: 16, bottom: 30 };
      const iw = Math.max(10, w - pad.left - pad.right);
      const ih = Math.max(10, h - pad.top - pad.bottom);
      const maxValue = Math.max(...values, 1);
      const yTicks = ticks(0, maxValue, 4);
      const yMax = yTicks[yTicks.length - 1];
      const slot = iw / values.length;
      const barW = Math.max(2, slot * 0.72);

      let svg = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"
        font-family="Inter, system-ui, sans-serif">`;

      yTicks.forEach((v) => {
        const y = pad.top + ih - (v / yMax) * ih;
        svg += `<line x1="${pad.left}" y1="${y}" x2="${w - pad.right}" y2="${y}"
          stroke="rgba(36,31,32,0.08)"/>`;
        svg += `<text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11"
          fill="#8A7C78">${fmt(v, v % 1 === 0 ? 0 : 1)}</text>`;
      });

      values.forEach((v, i) => {
        const barH = Math.max(1, (v / yMax) * ih);
        const x = pad.left + slot * i + (slot - barW) / 2;
        const y = pad.top + ih - barH;
        svg += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}"
          height="${barH.toFixed(1)}" rx="2.5" fill="${config.color || '#6E1B2B'}"
          opacity="${0.42 + 0.5 * (i / Math.max(1, values.length - 1))}"/>`;
      });

      const labels = config.labels || [];
      const stride = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor(iw / 56))));
      labels.forEach((label, i) => {
        if (i % stride) return;
        svg += `<text x="${(pad.left + slot * i + slot / 2).toFixed(1)}" y="${h - 10}"
          text-anchor="middle" font-size="10.5" fill="#8A7C78">${esc(label)}</text>`;
      });

      svg += `<line x1="${pad.left}" y1="${pad.top + ih}" x2="${w - pad.right}"
        y2="${pad.top + ih}" stroke="rgba(36,31,32,0.16)"/>`;
      svg += '</svg>';
      host.innerHTML = svg;
    };
    mount(host, draw);
  }

  /* ------------------------------------------------------------------ gauge */
  function gauge(host, config) {
    const draw = () => {
      const w = host.clientWidth || 260;
      const h = host.clientHeight || 230;
      if (w < 40) return;
      const size = Math.min(w, h * 1.05);
      const cx = w / 2;
      const cy = h / 2 + size * 0.04;
      const r = size * 0.35;
      const stroke = size * 0.068;
      const value = num(config.value);
      const max = config.max || 100;
      const fraction = Math.max(0, Math.min(1, value / max));

      const start = Math.PI * 0.75;
      const sweep = Math.PI * 1.5;
      const arc = (from, to) => {
        const x1 = cx + r * Math.cos(from);
        const y1 = cy + r * Math.sin(from);
        const x2 = cx + r * Math.cos(to);
        const y2 = cy + r * Math.sin(to);
        const large = to - from > Math.PI ? 1 : 0;
        return `M${x1.toFixed(1)},${y1.toFixed(1)} A${r},${r} 0 ${large} 1
          ${x2.toFixed(1)},${y2.toFixed(1)}`;
      };

      const colour = value < (config.threshold || 25) ? '#A3242A'
        : value < (config.warn || 45) ? '#A9641B' : '#2E6B4F';

      let svg = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"
        font-family="Inter, system-ui, sans-serif">`;
      svg += `<path d="${arc(start, start + sweep)}" fill="none"
        stroke="rgba(36,31,32,0.08)" stroke-width="${stroke}" stroke-linecap="round"/>`;
      if (fraction > 0.001) {
        svg += `<path d="${arc(start, start + sweep * fraction)}" fill="none"
          stroke="${colour}" stroke-width="${stroke}" stroke-linecap="round"/>`;
      }

      svg += `<text x="${cx}" y="${cy + size * 0.02}" text-anchor="middle"
        font-family="Playfair Display, Georgia, serif" font-size="${size * 0.21}"
        font-weight="600" fill="${colour}">${value ? fmt(value, 1) : '\u2014'}</text>`;
      svg += `<text x="${cx}" y="${cy + size * 0.14}" text-anchor="middle"
        font-size="${size * 0.055}" fill="#8A7C78" letter-spacing="1.2">PERCENT</text>`;

      if (config.threshold) {
        const angle = start + sweep * (config.threshold / max);
        const x = cx + (r + stroke * 0.72) * Math.cos(angle);
        const y = cy + (r + stroke * 0.72) * Math.sin(angle);
        svg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${size * 0.012}"
          fill="#A3242A" opacity="0.7"/>`;
      }
      svg += '</svg>';
      host.innerHTML = svg;
    };
    mount(host, draw);
  }

  /* ------------------------------------------------------------------- ring */
  function ring(host, config) {
    const draw = () => {
      const w = host.clientWidth || 118;
      const h = host.clientHeight || 118;
      const size = Math.min(w, h);
      const cx = w / 2, cy = h / 2;
      const r = size * 0.4;
      const stroke = size * 0.1;
      const fraction = Math.max(0, Math.min(1, config.value || 0));

      let svg = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"
        font-family="Inter, system-ui, sans-serif">`;
      svg += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
        stroke="rgba(36,31,32,0.09)" stroke-width="${stroke}"/>`;
      const circumference = 2 * Math.PI * r;
      svg += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
        stroke="${config.color || '#6E1B2B'}" stroke-width="${stroke}"
        stroke-linecap="round" stroke-dasharray="${circumference}"
        stroke-dashoffset="${circumference * (1 - fraction)}"
        transform="rotate(-90 ${cx} ${cy})"/>`;
      svg += `<text x="${cx}" y="${cy + 4}" text-anchor="middle"
        font-family="Playfair Display, Georgia, serif" font-size="${size * 0.26}"
        font-weight="600" fill="#6E1B2B">${Math.round(fraction * 100)}%</text>`;
      svg += '</svg>';
      host.innerHTML = svg;
    };
    mount(host, draw);
  }

  return { line, bars, gauge, ring };
})();
