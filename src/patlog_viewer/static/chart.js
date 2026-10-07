/* Three-axis acceleration chart. Gaps longer than 1 s take no width.
   Only the panels inside the viewport are painted. Hover draws on a
   separate canvas, so moving the pointer does not redraw the traces.
   A zoomed-out view keeps one min/max pair per pixel, so spikes stay visible. */
function mountChart(canvas, panels, langOf) {
  const GAP = 1;
  const SLOT = 300;
  const Y0 = -48;
  const Y1 = 48;
  const RAIL = -39.127;
  const G = 9.8;
  const COLORS = { ax: "#2563eb", ay: "#0f766e", az: "#b45309" };
  const ctx = canvas.getContext("2d");
  const scroller = document.createElement("div");
  const spacer = document.createElement("div");
  const frame = document.createElement("div");
  scroller.style.position = "relative";
  spacer.style.position = "relative";
  frame.style.position = "sticky";
  frame.style.top = "0";
  frame.style.zIndex = "1";
  frame.style.background = "#fbfbfd";
  canvas.parentNode.insertBefore(scroller, canvas);
  scroller.appendChild(spacer);
  spacer.appendChild(frame);
  frame.appendChild(canvas);
  const overlay = document.createElement("canvas");
  overlay.style.position = "absolute";
  overlay.style.left = "0";
  overlay.style.top = "0";
  overlay.style.width = "100%";
  overlay.style.height = "100%";
  overlay.style.pointerEvents = "none";
  frame.appendChild(overlay);
  canvas.style.display = "block";
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  const octx = overlay.getContext("2d");
  let hover = null;
  let drag = null;
  let state = [];
  let boxes = [];
  let dirty = true;
  let scheduled = 0;
  let sized = { w: -1, h: -1, dpr: 0 };
  let overlayMark = null;
  let g = ctx;

  function prepare(list) {
    return (list || []).filter(function (panel) { return panel.t && panel.t.length; }).map(function (panel) {
      const u = compress(panel.t);
      const full1 = Math.max(u.length ? u[u.length - 1] : 0, 1e-3);
      return Object.assign({}, panel, { u: u, view0: 0, view1: full1, full0: 0, full1: full1 });
    });
  }

  function compress(t) {
    const u = new Array(t.length);
    if (!t.length) return u;
    u[0] = 0;
    for (let i = 1; i < t.length; i++) {
      const dt = t[i] - t[i - 1];
      u[i] = u[i - 1] + (dt > 0 && dt <= GAP ? dt : 0);
    }
    return u;
  }

  function contentHeight() {
    return state.length ? state.length * SLOT : 80;
  }

  function viewHeight() {
    const cap = Math.max(320, Math.floor(window.innerHeight * 0.72));
    return Math.min(contentHeight(), cap);
  }

  function syncSize() {
    const content = contentHeight();
    const view = viewHeight();
    scroller.style.height = view + "px";
    scroller.style.overflowY = content > view + 1 ? "auto" : "hidden";
    spacer.style.height = content + "px";
    frame.style.height = view + "px";
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = frame.clientWidth || 800;
    const h = frame.clientHeight || view;
    if (sized.w === w && sized.h === h && sized.dpr === dpr) return;
    sized = { w: w, h: h, dpr: dpr };
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    overlay.width = canvas.width;
    overlay.height = canvas.height;
    dirty = true;
  }

  function placeBoxes() {
    const scroll = scroller.scrollTop;
    const left = 64;
    const right = 18;
    const titleH = 28;
    const axisH = 22;
    const gap = 16;
    const plotH = SLOT - titleH - axisH - gap;
    boxes = [];
    state.forEach(function (panel, index) {
      const titleY = index * SLOT - scroll;
      if (titleY > sized.h || titleY + SLOT < 0) return;
      boxes.push(Object.assign({}, panel, {
        x: left,
        y: titleY + titleH,
        w: sized.w - left - right,
        h: plotH,
        titleY: titleY
      }));
    });
  }

  function present() {
    syncSize();
    placeBoxes();
    if (!state.length) {
      ctx.setTransform(sized.dpr, 0, 0, sized.dpr, 0, 0);
      ctx.clearRect(0, 0, sized.w, sized.h);
      return;
    }
    if (dirty) {
      paint(null);
      dirty = false;
    }
    drawHover();
  }

  function requestPresent() {
    if (scheduled) return;
    scheduled = requestAnimationFrame(function () {
      scheduled = 0;
      present();
    });
  }

  function paint(ids) {
    g = ctx;
    g.setTransform(sized.dpr, 0, 0, sized.dpr, 0, 0);
    const chosen = ids ? boxes.filter(function (b) { return ids.indexOf(b.id) >= 0; }) : boxes;
    if (!ids) g.clearRect(0, 0, sized.w, sized.h);
    else chosen.forEach(function (b) { g.clearRect(0, b.titleY, sized.w, b.h + 54); });
    const yTicks = [-40, -20, 0, 20, 40];
    const lang = langOf() === "en" ? "en" : "zh";
    for (const b of chosen) {
      g.fillStyle = "#1c1917";
      g.font = "600 14px Segoe UI, Microsoft YaHei, sans-serif";
      g.textAlign = "center";
      g.textBaseline = "middle";
      const title = b.title ? (b.title[lang] || b.title.zh || b.id) : b.id;
      g.fillText(title, b.x + b.w / 2, b.titleY + 14);
      g.fillStyle = "#fff";
      g.fillRect(b.x, b.y, b.w, b.h);
      g.strokeStyle = "#eceae6";
      g.lineWidth = 1;
      g.setLineDash([]);
      for (const v of yTicks) {
        const y = yOf(v, b);
        g.beginPath();
        g.moveTo(b.x, y);
        g.lineTo(b.x + b.w, y);
        g.stroke();
      }
      hline(b, RAIL, "#dc2626");
      hline(b, G, "#16a34a");
      strokeAxis(b, "ax");
      strokeAxis(b, "ay");
      strokeAxis(b, "az");
      g.strokeStyle = "#d6d3d1";
      g.setLineDash([]);
      g.strokeRect(b.x + 0.5, b.y + 0.5, b.w - 1, b.h - 1);
      g.fillStyle = "#57534e";
      g.font = "12px Segoe UI, Microsoft YaHei, sans-serif";
      g.textAlign = "right";
      g.textBaseline = "middle";
      for (const v of yTicks) g.fillText(String(v), b.x - 8, yOf(v, b));
      g.save();
      g.translate(16, b.y + b.h / 2);
      g.rotate(-Math.PI / 2);
      g.textAlign = "center";
      g.fillStyle = "#44403c";
      g.fillText("m/s²", 0, 0);
      g.restore();
      const span = b.view1 - b.view0;
      const step = tickStep(span);
      g.textAlign = "center";
      g.textBaseline = "top";
      g.fillStyle = "#57534e";
      let lastX = -1e9;
      for (const seg of segments(b)) {
        let t = Math.ceil(seg.t0 / step) * step;
        for (; t <= seg.t1 + 1e-6; t += step) {
          const u = seg.u0 + (t - seg.t0) / (seg.t1 - seg.t0) * (seg.u1 - seg.u0);
          if (u < b.view0 || u > b.view1) continue;
          const x = xOf(u, b);
          if (x < b.x + 36 || x > b.x + b.w - 36) continue;
          if (x - lastX < 64) continue;
          lastX = x;
          g.fillText(step >= 60 ? labelDate(t) : labelClock(t, true), x, b.y + b.h + 6);
        }
      }
    }
  }

  function drawHover() {
    octx.setTransform(sized.dpr, 0, 0, sized.dpr, 0, 0);
    if (overlayMark) {
      octx.clearRect(overlayMark.x, overlayMark.y, overlayMark.w, overlayMark.h);
      overlayMark = null;
    }
    if (!hover) return;
    const b = boxes.find(function (box) { return box.id === hover.id; });
    if (!b) return;
    const x = xOf(hover.u, b);
    const clock = civil(hover.t);
    const lines = [
      b.id + "  " + labelDate(hover.t) + ":" + pad(clock.s) + "." + pad(clock.ms, 3),
      "ax  " + b.ax[hover.i].toFixed(3),
      "ay  " + b.ay[hover.i].toFixed(3),
      "az  " + b.az[hover.i].toFixed(3)
    ];
    octx.font = "12px Segoe UI, Microsoft YaHei, sans-serif";
    const tw = Math.max.apply(null, lines.map(function (s) { return octx.measureText(s).width; }));
    const bw = tw + 16;
    const bh = lines.length * 16 + 10;
    let bx = x + 12;
    let by = b.y + 8;
    if (bx + bw > b.x + b.w) bx = x - bw - 12;
    overlayMark = { x: b.x - 10, y: b.y - 2, w: b.w + 20, h: b.h + 4 };
    octx.strokeStyle = "#44403c";
    octx.lineWidth = 1;
    octx.setLineDash([]);
    octx.beginPath();
    octx.moveTo(x, b.y);
    octx.lineTo(x, b.y + b.h);
    octx.stroke();
    ["ax", "ay", "az"].forEach(function (axis) {
      octx.beginPath();
      octx.arc(x, yOf(b[axis][hover.i], b), 3.2, 0, Math.PI * 2);
      octx.fillStyle = "#fff";
      octx.fill();
      octx.strokeStyle = COLORS[axis];
      octx.lineWidth = 2;
      octx.stroke();
    });
    octx.fillStyle = "rgba(28,25,23,0.92)";
    octx.fillRect(bx, by, bw, bh);
    octx.fillStyle = "#fff";
    octx.textAlign = "left";
    octx.textBaseline = "top";
    lines.forEach(function (s, i) { octx.fillText(s, bx + 8, by + 6 + i * 16); });
  }

  function xOf(u, b) { return b.x + (u - b.view0) / (b.view1 - b.view0) * b.w; }
  function yOf(v, b) { return b.y + (Y1 - v) / (Y1 - Y0) * b.h; }
  function uOf(px, b) { return b.view0 + (px - b.x) / b.w * (b.view1 - b.view0); }
  function tickStep(span) {
    const choices = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
    const target = span / 8;
    let best = choices[choices.length - 1];
    for (const c of choices) if (c >= target) { best = c; break; }
    return best;
  }
  function hline(b, value, color) {
    const y = yOf(value, b);
    g.save();
    g.beginPath();
    g.rect(b.x, b.y, b.w, b.h);
    g.clip();
    g.strokeStyle = color;
    g.setLineDash([6, 4]);
    g.lineWidth = 1.25;
    g.beginPath();
    g.moveTo(b.x, y);
    g.lineTo(b.x + b.w, y);
    g.stroke();
    g.restore();
  }
  function segments(b) {
    const out = [];
    let s = 0;
    for (let i = 1; i <= b.t.length; i++) {
      if (i === b.t.length || b.t[i] - b.t[i - 1] > GAP) {
        const u0 = b.u[s];
        const u1 = b.u[i - 1];
        if (u1 > u0 && u1 >= b.view0 && u0 <= b.view1) out.push({ t0: b.t[s], t1: b.t[i - 1], u0: u0, u1: u1 });
        s = i;
      }
    }
    return out;
  }
  function lowerBound(u, value) {
    let lo = 0;
    let hi = u.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (u[mid] < value) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }
  function strokeAxis(b, axis) {
    const values = b[axis];
    const u = b.u;
    const t = b.t;
    const n = t.length;
    if (!n || b.view1 <= b.view0) return;
    let i0 = lowerBound(u, b.view0);
    let i1 = lowerBound(u, b.view1);
    if (i0 > 0) i0 -= 1;
    if (i1 < n) i1 += 1;
    g.save();
    g.beginPath();
    g.rect(b.x, b.y, b.w, b.h);
    g.clip();
    g.beginPath();
    g.strokeStyle = COLORS[axis];
    g.lineWidth = axis === "az" ? 1.45 : 1.15;
    g.setLineDash([]);
    const span = Math.max(0, i1 - i0);
    const pixels = Math.max(1, Math.floor(b.w));
    if (span > pixels * 3) strokeEnvelope(b, values, i0, i1, pixels);
    else strokeRaw(b, values, i0, i1);
    g.stroke();
    g.restore();
  }
  function strokeRaw(b, values, i0, i1) {
    let started = false;
    let prev = null;
    for (let i = i0; i < i1; i++) {
      const x = xOf(b.u[i], b);
      if (x < b.x - 2 || x > b.x + b.w + 2) { started = false; prev = b.t[i]; continue; }
      const y = yOf(values[i], b);
      if (!started || (prev !== null && b.t[i] - prev > GAP)) {
        g.moveTo(x, y);
        started = true;
      } else g.lineTo(x, y);
      prev = b.t[i];
    }
  }
  function strokeEnvelope(b, values, i0, i1, pixels) {
    const scale = pixels / (b.view1 - b.view0);
    let drawing = false;
    let bucket = -1;
    let min = 0;
    let max = 0;
    let prev = null;
    function emit() {
      if (bucket < 0) return;
      const x = b.x + bucket + 0.5;
      const y1 = yOf(min, b);
      const y2 = yOf(max, b);
      if (!drawing) {
        g.moveTo(x, y1);
        drawing = true;
      }
      g.lineTo(x, y1);
      g.lineTo(x, y2);
    }
    for (let i = i0; i < i1; i++) {
      const gap = prev !== null && b.t[i] - prev > GAP;
      const next = Math.max(0, Math.min(pixels - 1, Math.floor((b.u[i] - b.view0) * scale)));
      if (gap || (bucket >= 0 && next !== bucket)) {
        emit();
        if (gap) drawing = false;
        bucket = next;
        min = max = values[i];
      } else if (bucket < 0) {
        bucket = next;
        min = max = values[i];
      } else {
        if (values[i] < min) min = values[i];
        if (values[i] > max) max = values[i];
      }
      prev = b.t[i];
    }
    emit();
  }
  function civil(sec) {
    const whole = Math.floor(sec);
    let ms = Math.round((sec - whole) * 1000);
    let base = whole;
    if (ms === 1000) { ms = 0; base += 1; }
    const d = new Date(base * 1000);
    return {
      mo: d.getUTCMonth() + 1,
      da: d.getUTCDate(),
      h: d.getUTCHours(),
      mi: d.getUTCMinutes(),
      s: d.getUTCSeconds(),
      ms: ms
    };
  }
  function pad(n, width) {
    const text = String(n);
    const size = width || 2;
    return text.length >= size ? text : "0".repeat(size - text.length) + text;
  }
  function labelClock(sec, withSec) {
    const c = civil(sec);
    const clock = pad(c.h) + ":" + pad(c.mi);
    return withSec ? clock + ":" + pad(c.s) : clock;
  }
  function labelDate(sec) {
    const c = civil(sec);
    return pad(c.mo) + "-" + pad(c.da) + " " + labelClock(sec, false);
  }
  function nearest(b, uval) {
    const u = b.u;
    let lo = 0;
    let hi = u.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (u[mid] < uval) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0 && Math.abs(u[lo - 1] - uval) < Math.abs(u[lo] - uval)) lo--;
    return lo;
  }
  function point(ev) {
    const r = canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }
  function hit(pt) {
    return boxes.find(function (b) { return pt.x >= b.x && pt.x <= b.x + b.w && pt.y >= b.y && pt.y <= b.y + b.h; }) || null;
  }
  function markDirty() { dirty = true; }

  canvas.addEventListener("mousemove", function (ev) {
    const pt = point(ev);
    if (!boxes.length) placeBoxes();
    if (drag && drag.panel) {
      const current = boxes.find(function (b) { return b.id === drag.panel.id; }) || boxes[0];
      const span = drag.v1 - drag.v0;
      const dt = (pt.x - drag.x) / current.w * span;
      let v0 = drag.v0 - dt;
      let v1 = drag.v1 - dt;
      const width = drag.panel.full1 - drag.panel.full0;
      if (v0 < drag.panel.full0) { v0 = drag.panel.full0; v1 = v0 + span; }
      if (v1 > drag.panel.full1) { v1 = drag.panel.full1; v0 = v1 - span; }
      if (span >= width - 1e-6) { v0 = drag.panel.full0; v1 = drag.panel.full1; }
      drag.panel.view0 = v0;
      drag.panel.view1 = v1;
      markDirty();
    }
    const under = hit(pt);
    if (under) {
      const i = nearest(under, uOf(pt.x, under));
      hover = { id: under.id, i: i, t: under.t[i], u: under.u[i] };
    } else hover = null;
    requestPresent();
  });
  canvas.addEventListener("mouseleave", function () {
    hover = null;
    drag = null;
    requestPresent();
  });
  canvas.addEventListener("mousedown", function (ev) {
    const pt = point(ev);
    const b = hit(pt);
    if (!b) return;
    const src = state.find(function (p) { return p.id === b.id; });
    drag = { x: pt.x, v0: src.view0, v1: src.view1, panel: src };
  });
  window.addEventListener("mouseup", function () { drag = null; });
  canvas.addEventListener("dblclick", function () {
    state.forEach(function (p) { p.view0 = p.full0; p.view1 = p.full1; });
    markDirty();
    requestPresent();
  });
  canvas.addEventListener("wheel", function (ev) {
    const pt = point(ev);
    const b = hit(pt);
    if (!b) return;
    ev.preventDefault();
    const src = state.find(function (p) { return p.id === b.id; });
    const u = uOf(pt.x, b);
    const factor = ev.deltaY > 0 ? 1.2 : 1 / 1.2;
    let span = (src.view1 - src.view0) * factor;
    const maxSpan = src.full1 - src.full0;
    span = Math.max(8, Math.min(maxSpan, span));
    const ratio = (u - src.view0) / (src.view1 - src.view0 || 1);
    src.view0 = u - span * ratio;
    src.view1 = src.view0 + span;
    if (src.view0 < src.full0) { src.view0 = src.full0; src.view1 = src.full0 + span; }
    if (src.view1 > src.full1) { src.view1 = src.full1; src.view0 = src.full1 - span; }
    markDirty();
    requestPresent();
  }, { passive: false });
  scroller.addEventListener("scroll", function () {
    markDirty();
    requestPresent();
  }, { passive: true });
  window.addEventListener("resize", function () { requestPresent(); });

  state = prepare(panels);
  window.__redraw = function () { markDirty(); requestPresent(); };
  requestPresent();
  return {
    setPanels: function (next) {
      state = prepare(next);
      hover = null;
      boxes = [];
      sized.w = -1;
      scroller.scrollTop = 0;
      markDirty();
      requestPresent();
    }
  };
}
