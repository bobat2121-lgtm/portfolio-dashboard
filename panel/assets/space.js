/* Pixel-art space behind the dashboard.
 *
 * Everything is drawn into a low-resolution canvas (one art pixel = PIX x PIX screen pixels) and scaled
 * up with image-rendering: pixelated. Layers, back to front: nebula + stars, distant Star Wars planets,
 * far events, the solar system (sun, orbits, nine planets with real phases), mid events, the Death Star
 * and its TIE patrol, the rebel fleet, near events. Random events fire every 12-72 s. Very rarely
 * the event is a black hole that swallows everything, dashboard included, until the page is reloaded.
 *
 * Test hooks: window.__space.trigger("supernova" | "blackhole" | ...), or ?space=EVENT_NAME in the URL;
 * window.__space.active() lists the events running now; window.__space.deathStar() gives the superlaser's
 * dish, focus and emitter points in canvas pixels.
 * Keep "less-than followed by a letter or slash" out of this file: Streamlit's sanitizer drops any script
 * that looks like it hides a tag (panel/theme.py checks for it).
 */
(function () {
  "use strict";
  var CFG = __SPACE_CFG__;
  document.documentElement.setAttribute("data-space-style", CFG.style);
  if (window.__space && window.__space.alive) { window.__space.configure(CFG); return; }

  const PIX = 3;
  const TAU = Math.PI * 2;
  const reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  // ------------------------------------------------------------------ utils
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const lerp = (a, b, t) => a + (b - a) * t;
  const smooth = (t) => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
  const rnd = (a, b) => a + Math.random() * (b - a);
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);
  const bayer = (x, y) => BAYER[((y & 3) << 2) | (x & 3)];
  function hex(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  const ramp = (...hs) => hs.map(hex);
  const mix = (a, b, t) => [lerp(a[0], b[0], t) | 0, lerp(a[1], b[1], t) | 0, lerp(a[2], b[2], t) | 0];
  const rgba = (c, a) => "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + clamp(a, 0, 1).toFixed(3) + ")";
  // Ordered-dither pick along a color ramp: the classic pixel-art shading look.
  function pick(r, t, x, y) {
    t = clamp(t, 0, 0.9999) * (r.length - 1);
    const i = t | 0;
    return r[t - i > bayer(x, y) ? Math.min(i + 1, r.length - 1) : i];
  }
  function hash2(x, y) {
    let h = Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }
  const norm = (v) => { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; };
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

  function makeNoise(seed) {
    const r = mulberry32(seed), perm = new Uint8Array(512), vals = new Float32Array(256);
    const p = Array.from({ length: 256 }, (_, i) => i);
    for (let i = 255; i > 0; i--) { const j = (r() * (i + 1)) | 0; const t = p[i]; p[i] = p[j]; p[j] = t; }
    for (let i = 0; i < 512; i++) perm[i] = p[i & 255];
    for (let i = 0; i < 256; i++) vals[i] = r();
    const h = (x, y, z) => vals[perm[perm[perm[x & 255] + (y & 255)] + (z & 255)]];
    const s = (t) => t * t * (3 - 2 * t);
    function n3(x, y, z) {
      const X = Math.floor(x), Y = Math.floor(y), Z = Math.floor(z);
      const fx = s(x - X), fy = s(y - Y), fz = s(z - Z);
      const a = lerp(h(X, Y, Z), h(X + 1, Y, Z), fx), b = lerp(h(X, Y + 1, Z), h(X + 1, Y + 1, Z), fx);
      const c = lerp(h(X, Y, Z + 1), h(X + 1, Y, Z + 1), fx), d = lerp(h(X, Y + 1, Z + 1), h(X + 1, Y + 1, Z + 1), fx);
      return lerp(lerp(a, b, fy), lerp(c, d, fy), fz);
    }
    return function (x, y, z, oct) {
      let v = 0, amp = 0.5, f = 1, n = 0;
      for (let i = 0; i < (oct || 3); i++) { v += amp * n3(x * f, y * f, z * f); n += amp; amp *= 0.5; f *= 2.03; }
      return v / n;
    };
  }
  const noise = makeNoise(20260927);

  function canvas(w, h) { const c = document.createElement("canvas"); c.width = Math.max(1, w | 0); c.height = Math.max(1, h | 0); return c; }
  function screenCanvas(id, z) {
    let c = document.getElementById(id);
    if (!c) { c = document.createElement("canvas"); c.id = id; document.body.appendChild(c); }
    c.style.cssText = "position:fixed;left:0;top:0;z-index:" + z + ";pointer-events:none;image-rendering:pixelated;";
    return c;
  }
  function paint(size, fn) { // per-pixel sprite painter: fn(x, y) -> [r,g,b(,a)] | null
    const w = Array.isArray(size) ? size[0] : size, h = Array.isArray(size) ? size[1] : size;
    const c = canvas(w, h), ctx = c.getContext("2d"), img = ctx.createImageData(c.width, c.height), d = img.data;
    for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
      const col = fn(x, y); if (!col) continue;
      const i = (y * c.width + x) * 4;
      d[i] = col[0]; d[i + 1] = col[1]; d[i + 2] = col[2]; d[i + 3] = col.length > 3 ? col[3] : 255;
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }
  function line(ctx, x0, y0, x1, y1, col, a0, a1, w) {
    const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0)));
    for (let i = 0; i <= n; i++) {
      const t = i / n; ctx.fillStyle = rgba(col, lerp(a0, a1, t));
      ctx.fillRect(Math.round(lerp(x0, x1, t)), Math.round(lerp(y0, y1, t)), w || 1, w || 1);
    }
  }

  // ------------------------------------------------------------------ palettes
  const P = {
    merc: ramp("#141211", "#2e2a27", "#4f4943", "#766d64", "#9c9186", "#c2b8ac"),
    ven: ramp("#1f170c", "#4d3b1f", "#80673a", "#b39461", "#dcc18e", "#f3e3bb"),
    ocean: ramp("#030a1f", "#082047", "#0f3877", "#1b58a8", "#3b86d1", "#86c2ee"),
    land: ramp("#0b1a0a", "#1b3514", "#315a22", "#557f33", "#8a9a4e", "#b9ac72"),
    desert: ramp("#1f140a", "#4a3219", "#7b5a30", "#a9824d", "#cfab74", "#e6c996"),
    ice: ramp("#2a3440", "#5b6b7d", "#95a7b8", "#c9d6e2", "#eef4fa", "#ffffff"),
    cloud: ramp("#2c3036", "#60666e", "#9aa0a8", "#cdd2d8", "#f2f4f6", "#ffffff"),
    mars: ramp("#1a0905", "#431a0c", "#733016", "#a24c24", "#c96e3d", "#e59a68"),
    jtan: ramp("#24190f", "#523a23", "#83623f", "#b58f66", "#dcc09a", "#f1e2c6"),
    jbrown: ramp("#1f1009", "#452414", "#703d22", "#9b5a35", "#c27a52", "#dca07b"),
    jred: ramp("#2b0d07", "#5e1a0e", "#93301b", "#bf4b2c", "#df7250", "#f09a7a"),
    sat: ramp("#231e12", "#4f4429", "#7f6f47", "#ae9a6a", "#d3c291", "#ece0b8"),
    ring: ramp("#2a2519", "#56492f", "#86754f", "#b4a177", "#d9c89f", "#f0e6c8"),
    ura: ramp("#07222a", "#114650", "#1e6f7a", "#3a9ea6", "#72c9cf", "#b6ebee"),
    nep: ramp("#040c2e", "#0a2060", "#123a96", "#1f5ac2", "#4684df", "#86b3f3"),
    nepd: ramp("#02061a", "#061340", "#0c2566", "#153a8a"),
    plu: ramp("#1a1410", "#3a2d23", "#665142", "#967c66", "#c3aa90", "#e6d6c1"),
    pluh: ramp("#3a322b", "#6e6254", "#a89884", "#d8cbb7", "#f4ecdf", "#ffffff"),
    tat: ramp("#1e1309", "#4a3016", "#7d5427", "#b0803f", "#d7aa62", "#f0d193"),
    mus: ramp("#0b0605", "#1c0f0b", "#2f1a13", "#472a1f", "#5e3a2b"),
    lava: ramp("#5a1305", "#a8290a", "#e8560f", "#ff9b2e", "#ffd36b"),
    endor: ramp("#08140c", "#12301a", "#1f4f2a", "#317340", "#4f9a58", "#86c07f"),
    grey: ramp("#0d0e11", "#181a1e", "#23262b", "#30343a", "#40454c", "#535962", "#686f78", "#80878f", "#9aa0a8", "#b5bac0", "#cfd3d8"),
    sun: ramp("#ff6a10", "#ff9224", "#ffbb44", "#ffdf78", "#fff3c0", "#ffffff"),
    disk: ramp("#3a0a02", "#7a1c05", "#c2410c", "#f07c1e", "#ffc05a", "#fff1c9", "#ffffff"),
    nova: ramp("#3b1a5e", "#7a2a6e", "#c2413d", "#f08a2a", "#ffd35c", "#ffffff"),
    rock: ramp("#141210", "#2b2724", "#4a443e", "#6d655d", "#928a80"),
  };
  const WHITE = hex("#ffffff"), GREEN = hex("#5dff7a"), SABER = hex("#39ff14"), WARM = hex("#ffd98a");

  // ------------------------------------------------------------------ canvases + state
  const bgC = screenCanvas("space-bg", -1), g = bgC.getContext("2d");
  const fxC = screenCanvas("space-fx", 2147483000), fg = fxC.getContext("2d");
  fxC.style.display = "none";
  let W = 0, H = 0, S = 1, lay = {};
  let neb, starsStatic, orbitsC, stars = [], twinklers = [];
  let sunSprite, ds, fleet = [], far = [];
  const events = [];
  let BH = null, consumed = false, raf = 0, timer = 0, last = 0;
  const forced = {};

  // ------------------------------------------------------------------ sphere renderer
  // tex(vx, vy, vz, lat, lon, x, y) -> [ramp, bias, emissiveColor?]; L is the light direction
  // (x right, y down, z toward the viewer). Returns a canvas sprite.
  function sphere(R, tex, L, spin, o) {
    o = o || {};
    const pad = Math.ceil(o.pad || 0), size = Math.ceil(R * 2 + 2) + pad * 2, c = size / 2;
    const cs = Math.cos(spin || 0), sn = Math.sin(spin || 0);
    return paint(size, (x, y) => {
      const dx = x - c + 0.5, dy = y - c + 0.5;
      if (o.front) { const f = o.front(dx, dy, x, y); if (f) return f; }
      const px = dx / R, py = dy / R, rr = px * px + py * py;
      if (rr > 1) return o.back ? o.back(dx, dy, x, y) : null;
      const pz = Math.sqrt(1 - rr);
      const vx = px * cs + pz * sn, vz = -px * sn + pz * cs, vy = py;
      const lat = Math.asin(clamp(-vy, -1, 1)), lon = Math.atan2(vx, vz);
      const lum = Math.max(0, px * L[0] + py * L[1] + pz * L[2]);
      const [rmp, bias, glow] = tex(vx, vy, vz, lat, lon, x, y);
      if (glow) return glow;
      let col = pick(rmp, (o.ambient ?? 0.1) + lum * (o.gain ?? 0.92) + (bias || 0), x, y);
      if (o.atmo && rr > 0.82 && lum > 0.04) col = mix(col, o.atmo, 0.4 * lum);
      return col;
    });
  }

  const TEX = {
    mercury: (vx, vy, vz) => [P.merc, (noise(vx * 4 + 10, vy * 4, vz * 4, 3) - 0.5) * 0.6],
    venus: (vx, vy, vz) => [P.ven, (noise(vx * 2, vy * 7, vz * 2, 3) - 0.5) * 0.4],
    earth: (vx, vy, vz, lat) => {
      if (Math.abs(lat) > 1.2) return [P.ice, 0.05];
      if (noise(vx * 3.2 + 50, vy * 5, vz * 3.2, 3) > 0.62) return [P.cloud, 0.06];
      const n = noise(vx * 1.8 + 3, vy * 1.8, vz * 1.8, 4);
      if (n > 0.535) return [n > 0.6 ? P.desert : P.land, (n - 0.55) * 0.6];
      return [P.ocean, (n - 0.45) * 0.3];
    },
    mars: (vx, vy, vz, lat) => {
      if (lat > 1.25 || lat < -1.32) return [P.ice, 0];
      const n = noise(vx * 3 + 7, vy * 3, vz * 3, 3);
      return [P.mars, (n - 0.5) * 0.5 - (n > 0.58 ? 0.12 : 0)];
    },
    jupiter: (vx, vy, vz, lat, lon) => {
      const turb = noise(vx * 2.5, vy * 9, vz * 2.5, 3);
      const sLat = lat + 0.36, sLon = ((lon - 0.8 + Math.PI) % TAU + TAU) % TAU - Math.PI;
      if ((sLat * sLat) / 0.014 + (sLon * sLon) / 0.06 < 1) return [P.jred, 0.02];
      const b = Math.sin(lat * 10 + turb * 3.2);
      return [b > 0.15 ? P.jtan : P.jbrown, b * 0.08 + (turb - 0.5) * 0.18];
    },
    saturn: (vx, vy, vz, lat) => [P.sat, Math.sin(lat * 14) * 0.06 + (noise(vx * 2, vy * 10, vz * 2, 2) - 0.5) * 0.12],
    uranus: (vx, vy, vz) => [P.ura, (noise(vx * 2, vy * 6, vz * 2, 2) - 0.5) * 0.1],
    neptune: (vx, vy, vz, lat, lon) => {
      const sLat = lat + 0.3, sLon = ((lon - 0.4 + Math.PI) % TAU + TAU) % TAU - Math.PI;
      if ((sLat * sLat) / 0.02 + (sLon * sLon) / 0.05 < 1) return [P.nepd, 0.05];
      return [P.nep, Math.sin(lat * 8) * 0.04 + (noise(vx * 2, vy * 7, vz * 2, 2) - 0.5) * 0.14];
    },
    pluto: (vx, vy, vz, lat, lon) => {
      const hLat = lat - 0.1, hLon = ((lon - 0.3 + Math.PI) % TAU + TAU) % TAU - Math.PI;
      if ((hLat * hLat) / 0.12 + (hLon * hLon) / 0.1 < 1) return [P.pluh, -0.05];
      return [P.plu, (noise(vx * 3, vy * 3, vz * 3, 3) - 0.5) * 0.4];
    },
    tatooine: (vx, vy, vz) => [P.tat, (noise(vx * 3 + 2, vy * 3, vz * 3, 3) - 0.5) * 0.5],
    hoth: (vx, vy, vz) => [P.ice, (noise(vx * 3 + 9, vy * 3, vz * 3, 3) - 0.5) * 0.35],
    mustafar: (vx, vy, vz, lat, lon, x, y) => {
      const n = noise(vx * 4 + 1, vy * 4, vz * 4, 3);
      if (Math.abs(n - 0.5) < 0.035) return [P.lava, 0, pick(P.lava, 0.35 + (0.035 - Math.abs(n - 0.5)) * 14, x, y)];
      return [P.mus, (n - 0.5) * 0.4];
    },
    endor: (vx, vy, vz, lat) => [P.endor, Math.sin(lat * 9 + noise(vx * 2, vy * 6, vz * 2, 2) * 2) * 0.08],
  };

  // ------------------------------------------------------------------ the sky
  function buildSky() {
    const r = mulberry32(W * 7919 + H * 31);
    const top = hex("#03040a"), bot = hex("#060914");
    const NEB = [hex("#2a1446"), hex("#14204a"), hex("#0d3140"), hex("#3a0f35"), hex("#1c2a5c")];
    const band = (x, y) => Math.max(0, 1 - Math.abs((y - H * 0.2) - (x - W * 0.05) * 0.42) / (H * 0.32));
    const c = canvas(W, H), ctx = c.getContext("2d"), img = ctx.createImageData(W, H), d = img.data;
    for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) {
      const f = noise(x * 0.012, y * 0.012, 3.1, 4), f2 = noise(x * 0.035 + 40, y * 0.035, 7.7, 2);
      const dens = clamp((f - 0.47) * 3.2 + band(x, y) * 0.3 + (f2 - 0.5) * 0.45, 0, 1);
      const col = NEB[Math.min(NEB.length - 1, (noise(x * 0.006 + 9, y * 0.006, 1.7, 2) * NEB.length * 1.3) | 0)];
      for (let yy = y; yy < Math.min(y + 2, H); yy++) for (let xx = x; xx < Math.min(x + 2, W); xx++) {
        const i = (yy * W + xx) * 4, t = yy / H;
        const a = dens * 0.5 > bayer(xx, yy) * 0.85 ? dens * 0.5 : dens * 0.18;
        d[i] = lerp(lerp(top[0], bot[0], t), col[0], a); d[i + 1] = lerp(lerp(top[1], bot[1], t), col[1], a);
        d[i + 2] = lerp(lerp(top[2], bot[2], t), col[2], a); d[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    neb = c;

    stars = []; twinklers = [];
    const TINTS = [hex("#ffffff"), hex("#dfe9ff"), hex("#fff1d6"), hex("#ffc9b5"), hex("#bcd4ff"), hex("#fff7a8")];
    const count = Math.round(((W * H) / 80) * (CFG.stars || 1));         // CFG.stars: density, 1 = the usual sky
    for (let i = 0; i < count; i++) {
      const x = (r() * W) | 0, y = (r() * H) | 0;
      if (band(x, y) < 0.25 && r() < 0.4) continue;
      const roll = r(), size = roll < 0.84 ? 1 : roll < 0.965 ? 2 : roll < 0.993 ? 3 : 4;
      const s = { x, y, size, c: TINTS[(r() * TINTS.length) | 0], a: size === 1 ? 0.22 + r() * 0.6 : 0.7 + r() * 0.3,
        tw: r() < 0.22, ph: r() * TAU, sp: 0.4 + r() * 1.8 };
      stars.push(s); if (s.tw) twinklers.push(s);
    }
    starsStatic = canvas(W, H);
    const sc = starsStatic.getContext("2d");
    for (const s of stars) if (!s.tw) drawStar(sc, s.x, s.y, s, s.a);
  }
  function drawStar(ctx, x, y, s, a) {
    x = Math.round(x); y = Math.round(y);
    ctx.fillStyle = rgba(s.c, a); ctx.fillRect(x, y, 1, 1);
    if (s.size < 2) return;
    ctx.fillStyle = rgba(s.c, a * 0.45);
    ctx.fillRect(x - 1, y, 1, 1); ctx.fillRect(x + 1, y, 1, 1); ctx.fillRect(x, y - 1, 1, 1); ctx.fillRect(x, y + 1, 1, 1);
    if (s.size < 3) return;
    ctx.fillStyle = rgba(s.c, a * 0.2);
    ctx.fillRect(x - 2, y, 1, 1); ctx.fillRect(x + 2, y, 1, 1); ctx.fillRect(x, y - 2, 1, 1); ctx.fillRect(x, y + 2, 1, 1);
    if (s.size < 4) return;
    ctx.fillStyle = rgba(s.c, a * 0.22);
    ctx.fillRect(x - 1, y - 1, 1, 1); ctx.fillRect(x + 1, y - 1, 1, 1); ctx.fillRect(x - 1, y + 1, 1, 1); ctx.fillRect(x + 1, y + 1, 1, 1);
    ctx.fillStyle = rgba(s.c, a * 0.1);
    ctx.fillRect(x - 3, y, 1, 1); ctx.fillRect(x + 3, y, 1, 1); ctx.fillRect(x, y - 3, 1, 1); ctx.fillRect(x, y + 3, 1, 1);
  }
  const twinkle = (s, now) => 0.5 + 0.5 * Math.sin(now * s.sp + s.ph);

  // ------------------------------------------------------------------ the solar system
  const PL = [
    { id: "mercury", a: 0.15, r: 1.7, T: 260, spin: 0.01 },
    { id: "venus", a: 0.23, r: 2.5, T: 470, spin: 0.004, atmo: "#fff0c0" },
    { id: "earth", a: 0.32, r: 2.8, T: 700, spin: 0.05, atmo: "#7fc0ff", moon: true },
    { id: "mars", a: 0.41, r: 2.1, T: 980, spin: 0.05 },
    { id: "jupiter", a: 0.55, r: 6.8, T: 1700, spin: 0.09 },
    { id: "saturn", a: 0.69, r: 5.6, T: 2400, spin: 0.08, rings: true },
    { id: "uranus", a: 0.81, r: 4.1, T: 3100, spin: 0.06, atmo: "#c8f4f5" },
    { id: "neptune", a: 0.91, r: 4.0, T: 3700, spin: 0.06, atmo: "#9ec4ff" },
    { id: "pluto", a: 1.0, r: 1.3, T: 4600, spin: 0.02 },
  ];
  PL.forEach((p, i) => { p.th0 = mulberry32(1000 + i)() * TAU; p.sprite = null; p.rendered = -1; });

  function buildSun() {
    const R = Math.max(3, Math.round(5 * S)), G = Math.round(R * 3.2), size = G * 2 + 1;
    const glowC = hex("#ffb347");
    sunSprite = { G, c: paint(size, (x, y) => {
      const dx = x - G, dy = y - G, d = Math.hypot(dx, dy) / R;
      if (d <= 1) return pick(P.sun, 1 - d * 0.6 + (noise(x * 0.6, y * 0.6, 1, 2) - 0.5) * 0.25, x, y);
      const k = 1 - (d - 1) / (G / R - 1); if (k <= 0) return null;
      const a = k * k * 0.6;
      return a * 1.15 > bayer(x, y) ? [glowC[0], glowC[1], glowC[2], (a * 255) | 0] : null;
    }) };
  }
  function buildOrbits() {
    orbitsC = canvas(W, H);
    const ctx = orbitsC.getContext("2d"), col = hex("#8193bd");
    for (const p of PL) {
      const rx = p.a * lay.orbit.rx, ry = rx * lay.orbit.tilt, n = Math.max(48, (TAU * rx / 5) | 0);
      for (let i = 0; i < n; i++) {
        const th = (i / n) * TAU;
        ctx.fillStyle = rgba(col, Math.sin(th) > 0 ? 0.13 : 0.07);
        ctx.fillRect(Math.round(lay.sun.x + Math.cos(th) * rx), Math.round(lay.sun.y + Math.sin(th) * ry), 1, 1);
      }
    }
  }
  function planetPos(p, t) {
    const th = p.th0 + (TAU * t) / p.T, rx = p.a * lay.orbit.rx;
    return { x: lay.sun.x + Math.cos(th) * rx, y: lay.sun.y + Math.sin(th) * rx * lay.orbit.tilt, z: Math.sin(th), th, rx };
  }
  function renderPlanet(p, t) {
    const pos = planetPos(p, t), sinT = lay.orbit.tilt, cosT = Math.sqrt(1 - sinT * sinT);
    const X = Math.cos(pos.th) * pos.rx, Z = Math.sin(pos.th) * pos.rx;
    const L = norm([-X, -Z * sinT, -Z * cosT]);            // toward the sun, in view space
    const R = Math.max(1.2, p.r * S), o = { atmo: p.atmo ? hex(p.atmo) : null, ambient: 0.1 };
    if (p.rings) {
      const ring = (dx, dy, x, y) => {
        const e = Math.hypot(dx, dy / 0.36) / R;
        if (e < 1.3 || e > 2.25 || (e > 1.84 && e < 1.93)) return null;
        const lit = 0.45 + 0.4 * clamp((dx * L[0] + dy * L[1]) / (Math.hypot(dx, dy) || 1), -1, 1);
        return pick(P.ring, (0.55 + 0.25 * Math.sin(e * 23)) * lit + 0.1, x, y);
      };
      o.pad = R * 1.3;
      o.front = (dx, dy, x, y) => (dy >= 0 ? ring(dx, dy, x, y) : null);
      o.back = (dx, dy, x, y) => (dy < 0 ? ring(dx, dy, x, y) : null);
    }
    p.sprite = sphere(R, TEX[p.id], L, t * p.spin, o);
    p.rendered = performance.now();
  }
  function drawPlanet(ctx, p, t, bh, now) {
    if (!p.sprite) return;
    const pos = planetPos(p, t);
    let x = pos.x, y = pos.y, sc = 1;
    if (bh) { const w = warp(x, y, bh); if (!w) return; [x, y, sc] = w; }
    const s = p.sprite, w = Math.max(1, Math.round(s.width * sc)), h = Math.max(1, Math.round(s.height * sc));
    ctx.drawImage(s, Math.round(x - w / 2), Math.round(y - h / 2), w, h);
    if (p.moon && sc === 1) {
      const R = p.r * S, a = (now * TAU) / 45, mx = Math.cos(a) * (R + 3), my = Math.sin(a) * (R + 3) * 0.35;
      if (my > 0 || Math.hypot(mx, my) > R + 0.5) { ctx.fillStyle = "#b9bcc2"; ctx.fillRect(Math.round(x + mx), Math.round(y + my), 1, 1); }
    }
  }

  // Distant planets from the other galaxy, far away, turning slowly.
  function buildFar() {
    const specs = [
      { id: "tatooine", x: 0.05, y: 0.12, r: 4.2, L: [0.8, -0.4, 0.45], suns: true },
      { id: "hoth", x: 0.08, y: 0.9, r: 3.6, L: [0.6, -0.6, 0.5] },
      { id: "mustafar", x: 0.95, y: 0.07, r: 3.2, L: [-0.7, 0.2, 0.7] },
      { id: "endor", x: 0.93, y: 0.94, r: 5.2, L: [-0.6, -0.5, 0.6], moon: true },
    ];
    far = specs.map((s, i) => ({ ...s, ph: i * 1.7, sprite: null, rendered: -1 }));
  }
  function renderFar(f, t) { f.sprite = sphere(Math.max(2, f.r * S), TEX[f.id], norm(f.L), t * 0.03, { ambient: 0.08 }); f.rendered = performance.now(); }
  function drawFar(ctx, t, now, bh) {
    for (const f of far) {
      if (!f.sprite) continue;
      let x = f.x * W + Math.cos(now / 60 + f.ph) * 2, y = f.y * H + Math.sin(now / 60 + f.ph) * 1.5, sc = 1;
      if (bh) { const w = warp(x, y, bh); if (!w) continue; [x, y, sc] = w; }
      const w = Math.max(1, Math.round(f.sprite.width * sc));
      ctx.drawImage(f.sprite, Math.round(x - w / 2), Math.round(y - w / 2), w, w);
      if (sc < 1) continue;
      if (f.suns) {
        const R = f.r * S;
        drawStar(ctx, x + R * 3.2, y - R * 2.4, { size: 3, c: hex("#fff3a8") }, 0.95);
        drawStar(ctx, x + R * 4.6, y - R * 1.3, { size: 2, c: hex("#ffb36b") }, 0.9);
      }
      if (f.moon) {
        const R = f.r * S, a = now / 20;
        ctx.fillStyle = "#4f9a58"; ctx.fillRect(Math.round(x + Math.cos(a) * (R + 4)), Math.round(y + Math.sin(a) * (R + 4) * 0.4), 1, 1);
      }
    }
  }

  // ------------------------------------------------------------------ the Death Star
  function buildDeathStar() {
    const R = lay.ds.r, size = R * 2 + 3, c = size / 2;
    const L = norm([0.78, -0.3, 0.55]);                       // lit from the sun, off to its right
    const lat0 = 0.56, lon0 = 0.62;                           // superlaser dish, upper right hemisphere
    const dishV = [Math.cos(lat0) * Math.sin(lon0), -Math.sin(lat0), Math.cos(lat0) * Math.cos(lon0)];
    const dishA = 0.25, lights = [];
    const sprite = paint(size, (x, y) => {
      const px = (x - c + 0.5) / R, py = (y - c + 0.5) / R, rr = px * px + py * py;
      if (rr > 1) return null;
      const pz = Math.sqrt(1 - rr), n = [px, py, pz];
      const lat = Math.asin(-py), lon = Math.atan2(px, pz);
      let lum = Math.max(0, dot(n, L)), bias = 0;
      const cellW = 0.085 / Math.max(0.25, Math.cos(lat));
      bias += (hash2(Math.floor((lon + 4) / cellW), Math.floor((lat + 2) / 0.07)) - 0.5) * 0.13;
      if (Math.abs(((lat + 3) / 0.16) % 1 - 0.5) < 0.05) bias -= 0.05;                    // latitude seams
      if (Math.abs(lat) < 0.03) { lum *= 0.3; bias -= 0.12; }                              // equatorial trench
      else if (lat < 0 && lat > -0.055) bias += 0.09;                                     // its lit lower lip
      const ang = Math.acos(clamp(dot(n, dishV), -1, 1));
      if (ang < dishA) {
        const q = ang / dishA;
        if (q < 0.1) return q < 0.05 ? GREEN : hex("#16351f");                            // focus lens
        const nc = norm([dishV[0] - (n[0] - dishV[0]) * 2.6, dishV[1] - (n[1] - dishV[1]) * 2.6, dishV[2] - (n[2] - dishV[2]) * 2.6]);
        lum = Math.max(0, dot(nc, L)) * 0.85; bias = 0;
        if (q > 0.9) lum = Math.max(lum, 0.72);                                           // bright rim
        if (Math.abs(q - 0.55) < 0.05 || Math.abs(q - 0.3) < 0.05) bias -= 0.07;          // concentric rings
      }
      const col = pick(P.grey, 0.08 + lum * 0.9 + bias, x, y);
      if (lum < 0.1 && hash2(x * 3 + 1, y * 7 + 2) > 0.975) { lights.push([x, y]); return mix(col, WARM, 0.55); }
      return col;
    });
    // Superlaser points, with the same projection the sprite is painted with (a point n on the sphere
    // lands at the center + n.xy * R): emitters around the tilted dish, the focus just in front of it.
    const proj = (n, k = 1) => [lay.ds.x + n[0] * R * k, lay.ds.y + n[1] * R * k];
    const u = norm([-dishV[1], dishV[0], 0]);
    const v = [dishV[1] * u[2] - dishV[2] * u[1], dishV[2] * u[0] - dishV[0] * u[2], dishV[0] * u[1] - dishV[1] * u[0]];
    const onDish = (q, phi) => {                               // q: 0 at the lens, 1 at the rim
      const s = Math.sin(q * dishA), c = Math.cos(q * dishA), cp = Math.cos(phi), sp = Math.sin(phi);
      return proj([c * dishV[0] + s * (cp * u[0] + sp * v[0]), c * dishV[1] + s * (cp * u[1] + sp * v[1])]);
    };
    ds = { ...lay.ds, sprite, size, lights, onDish, dish: proj(dishV), focus: proj(dishV, 1.1),
      emitters: Array.from({ length: 8 }, (_, i) => onDish(0.8, (i / 8) * TAU)) };
  }
  const TIE = ["D...D", "D.G.D", "DGKGD", "D.G.D", "D...D"];
  function drawDeathStar(ctx, now, bh) {
    const tieS = ds.tie || (ds.tie = fromMap(TIE));
    const ties = [0, Math.PI * 0.85].map((off) => {
      const a = (now * TAU) / 80 + off;
      return { x: ds.x + Math.cos(a) * ds.r * 1.55, y: ds.y + Math.sin(a) * ds.r * 0.32 - ds.r * 0.15, front: Math.sin(a) > 0 };
    });
    const drawTie = (t) => {
      let x = t.x, y = t.y, sc = 1;
      if (bh) { const w = warp(x, y, bh); if (!w) return; [x, y, sc] = w; }
      ctx.drawImage(tieS.c, Math.round(x - 2), Math.round(y - 2), Math.max(1, Math.round(5 * sc)), Math.max(1, Math.round(5 * sc)));
    };
    ties.filter((t) => !t.front).forEach(drawTie);
    let x = ds.x, y = ds.y, sc = 1;
    if (bh) { const w = warp(x, y, bh); if (w) { [x, y, sc] = w; } else sc = 0; }
    if (sc > 0) {
      const s = Math.max(1, Math.round(ds.size * sc)), ox = Math.round(x - s / 2), oy = Math.round(y - s / 2);
      ctx.drawImage(ds.sprite, ox, oy, s, s);
      if (sc === 1) for (let i = 0; i < ds.lights.length; i += 3) {
        const [lx, ly] = ds.lights[(i + ((now * 2) | 0)) % ds.lights.length];
        ctx.fillStyle = rgba(WARM, 0.5 + 0.5 * Math.sin(now * 3 + i)); ctx.fillRect(ox + lx, oy + ly, 1, 1);
      }
    }
    ties.filter((t) => t.front).forEach(drawTie);
  }

  // ------------------------------------------------------------------ the rebel fleet
  // Side views, noses to the left: they fly at the Death Star. Engines glow and trail at the back.
  const PAL = { w: "#d8dade", g: "#9aa0a8", d: "#5b6068", k: "#23262b", r: "#c9342c", o: "#ff8a3d", y: "#ffd98a",
    c: "#1d2b3a", l: "#7c828b", Y: "#e3c24a", b: "#8fe3ff", B: "#3aa0e8", D: "#3a3f47", G: "#8a8f98", K: "#15171a",
    W: "#f4f5f7", R: "#8e1f1a", s: "#b8bdc4" };
  // T-65 X-wing, S-foils open: long nose, canopy, R2 unit, the near wings with engines at their roots and
  // cannons running forward from the tips.
  const XWING = [
    "........llllllllllllsgd.......",
    "...................sgwd.......",
    "..................sgwrd.......",
    ".................sgwwd........",
    "............cc..sDDDDDDDDDDDoy",
    ".........gsscccwBBgwwwwwwwwDoy",
    "...gswwwwwwwwwwwwwwwwwwwwwwwd.",
    "wWwwwwrrrrwwwwwwwwwwwwwwwwwwd.",
    "...ddddddddgggggggggggggggggd.",
    ".................dDDDDDDDDDDoy",
    ".................sgwwwDDDDDDoy",
    "..................sgwrd.......",
    "...................sgwd.......",
    "........llllllllllllsgd.......",
  ];
  // BTL Y-wing: the cockpit pod and ion turret, an open frame, then the long engine nacelle.
  const YWING = [
    "..ll...............................",
    ".sgws..............................",
    "sgYYwwgd...........................",
    "gwcckwwwwd.......Rsgggggggggggggg.",
    "gwwwwwwwwwwdkdkdkrswwwwwwwwwwwwwwDDoy",
    "gwwwwYYwwwwdkdkdkrswwwwwwwwwwwwwwDDoy",
    ".ddddddddddd.....Rsdddddddddddddd.",
  ];
  // RZ-1 A-wing: a flat wedge, canopy mid-body, fins over the engines.
  const AWING = [
    "................sgd..",
    "...............sgwd..",
    "..........cc..sgwwDoy",
    ".......gscckwwwwwwDoy",
    "...gswwwwrrwwwwwwwwd.",
    "wWwwwwwwwwwwwwwwwwwd.",
    "...dddddddddddddddDoy",
    "...............sgdDoy",
    "................sgd..",
  ];
  // The Millennium Falcon from a low three-quarter angle: the disc's top as a squashed ellipse over its
  // rim, the blue engine band across the back, forks forward, cockpit tube on the side, dish on top.
  function falconRows() {
    const Wd = 38, Hd = 15, cx = 23, cy = 6.5, rx = 13.5, ry = 4.3, rows = [];
    for (let y = 0; y !== Hd; y++) {
      let row = "";
      for (let x = 0; x !== Wd; x++) {
        const dx = x - cx + 0.5, dy = y - cy + 0.5, e = Math.hypot(dx / rx, dy / ry);
        const under = Math.hypot(dx / rx, (dy - 1) / ry) <= 1 || Math.hypot(dx / rx, (dy - 2) / ry) <= 1;
        let ch = ".";
        if (e <= 1) {
          const ring = Math.abs(e - 0.55) < 0.07 || Math.abs(e - 0.9) < 0.06;
          const spoke = e > 0.2 && Math.abs(((Math.atan2(dy / ry, dx / rx) + 7) % (Math.PI / 4)) - 0.4) < 0.09;
          const t = 0.62 - (dy / ry) * 0.22 - (dx / rx) * 0.08;
          ch = ring || spoke ? "g" : t > 0.8 ? "W" : t > 0.52 ? "w" : "s";
          if (e < 0.16) ch = "d";
        } else if (under) ch = dx > rx * 0.3 ? "b" : dy > 0 ? "d" : "D";
        if (x >= 2 && x <= 13) {                                   // the forks: far one high, near one low
          if (y === 5) ch = "s";
          if ((y === 8 || y === 9) && e > 1) ch = y === 8 ? "w" : "d";
        }
        if (x >= 13 && x <= 17 && y >= 9 && y <= 11) ch = x === 13 ? (y === 10 ? "c" : "k") : y === 9 ? "w" : y === 11 ? "d" : "s";
        if ((x === 27 && y === 2) || (x === 28 && (y === 1 || y === 2))) ch = "W";   // radar dish
        if ((x === 22 || x === 23) && (y === 5 || y === 6)) ch = "k";                // top turret
        row += ch;
      }
      rows.push(row);
    }
    return rows;
  }
  // rows -> sprite. glow: engine pixels that flicker; exhaust: glowing pixels at the back of a row, which
  // trail behind the ship; guns: where lasers leave (the tip of each cannon, else the nose).
  function fromMap(rows) {
    const h = rows.length, w = Math.max(...rows.map((r) => r.length)), glow = [], exhaust = [], guns = [];
    const c = paint([w, h], (x, y) => {
      const ch = rows[y][x];
      if (!ch || ch === ".") return null;
      if (ch === "o" || ch === "y" || ch === "b") glow.push([x, y, hex(PAL[ch])]);
      return hex(PAL[ch]);
    });
    let nose = null;
    rows.forEach((row, y) => {
      const end = row.replace(/\.+$/, "").length - 1;
      if (end >= 0 && "oyb".includes(row[end])) exhaust.push([end, y, hex(PAL[row[end]])]);
      if (row.includes("l")) guns.push([row.indexOf("l"), y]);
      const first = row.search(/[^.]/);
      if (first >= 0 && (!nose || first < nose[0])) nose = [first, y];
    });
    if (!guns.length && nose) guns.push(nose);
    return { c, w, h, glow, exhaust, guns, nose };
  }
  function monCal(Lc) {
    const Hc = Math.round(Lc * 0.26), h = Hc + 4, cy = h / 2, glow = [];
    const HULL = ramp("#0f1622", "#1c2839", "#2c3d55", "#415774", "#5b7493", "#7e97b3", "#a9bdd1");
    const haze = hex("#0a0f1a");
    const c = paint([Lc + 2, h], (x, y) => {
      const u = x / Lc; if (u > 1) return null;
      const half = (Hc / 2) * Math.pow(Math.sin(Math.PI * Math.min(1, u * 1.08 + 0.02)), 0.5) * (0.86 + 0.14 * Math.sin(u * 26));
      const dy = y - cy + 0.5; if (Math.abs(dy) > half) return null;
      if (x >= Lc - 2 && Math.abs(dy) < half * 0.8 && (y % 3) === 1) { glow.push([x, y, hex(PAL.b)]); return hex(PAL.b); }
      if ((y % 3) === 0 && (x % 4) === 1 && dy > -half * 0.6 && dy < half * 0.5 && hash2(x, y) < 0.55) return mix(hex("#ffe7a6"), haze, 0.35);
      const t = 0.55 - (dy / (half || 1)) * 0.32 + (noise(x * 0.2, y * 0.2, 5, 2) - 0.5) * 0.3;
      return mix(pick(HULL, t, x, y), haze, 0.22);
    });
    return { c, w: Lc + 2, h, glow };
  }
  function buildFleet() {
    const FX = lay.fleetX, s = S;
    const X = fromMap(XWING), Y = fromMap(YWING), A = fromMap(AWING), F = fromMap(falconRows());
    const M = monCal(Math.round(clamp(W * 0.1, 36, 84)));
    // fighters strafe the Death Star; fighters and the Falcon can jump to hyperspace; the cruiser holds station
    const ship = (sp, x, y, bob, kind) => ({ sp, x, y, ph: Math.random() * TAU, bob: bob ?? 1, hidden: false,
      fighter: kind === "fighter", jumper: kind !== "capital" });
    fleet = [
      ship(M, FX + 2 * s, H * 0.2, 0.3, "capital"), ship(F, FX - 4 * s, H * 0.64, 1, "freighter"),
      ship(X, FX - 12 * s, H * 0.42, 1, "fighter"), ship(X, FX + 4 * s, H * 0.33, 1, "fighter"),
      ship(X, FX + 6 * s, H * 0.5, 1, "fighter"), ship(Y, FX + 8 * s, H * 0.76, 1, "fighter"),
      ship(A, FX - 10 * s, H * 0.85, 1, "fighter"), ship(A, FX + 3 * s, H * 0.9, 1, "fighter"),
    ];
    for (const sh of fleet) sh.x = clamp(sh.x, sh.sp.w / 2 + 2, W - sh.sp.w / 2 - 2);
  }
  // During a rebel attack the fighters press in toward the Death Star, then pull back.
  let attack = null;
  function shipPos(sh, now) {
    let push = 0;
    if (attack && sh.fighter) {
      const t = now - attack.t0;
      push = smooth(t / 2.5) * (1 - smooth((t - attack.dur + 0.5) / 2.5)) * 18 * S;
    }
    return [sh.x - push + Math.sin(now * 0.05 + sh.ph) * 3 * S * (sh.fighter ? 1 : 0.3), sh.y + Math.round(Math.sin(now * 0.45 + sh.ph) * sh.bob)];
  }
  function drawFleet(ctx, now, bh) {
    for (const sh of fleet) {
      if (sh.hidden) continue;
      let [x, y] = shipPos(sh, now), sc = 1;
      if (bh) { const w = warp(x, y, bh); if (!w) continue; [x, y, sc] = w; }
      const w = Math.max(1, Math.round(sh.sp.w * sc)), h = Math.max(1, Math.round(sh.sp.h * sc));
      const ox = Math.round(x - w / 2), oy = Math.round(y - h / 2);
      ctx.drawImage(sh.sp.c, ox, oy, w, h);
      if (sc !== 1) continue;
      for (const [gx, gy, col] of sh.sp.glow) {
        ctx.fillStyle = rgba(col, 0.55 + Math.random() * 0.45); ctx.fillRect(ox + gx, oy + gy, 1, 1);
      }
      for (const [ex, ey, col] of sh.sp.exhaust || []) {                // the engine trail, flickering
        const n = 2 + ((Math.random() * 4) | 0);
        for (let i = 1; i <= n; i++) { ctx.fillStyle = rgba(col, (0.62 - i * 0.11) * (0.6 + Math.random() * 0.4)); ctx.fillRect(ox + ex + i, oy + ey, 1, 1); }
      }
    }
  }

  // ------------------------------------------------------------------ events
  function shootingStar(o) {
    o = o || {};
    const dir = o.dir ?? (Math.random() < 0.5 ? 1 : -1), ang = o.ang ?? rnd(0.25, 0.6), sp = o.sp ?? rnd(70, 110);
    const len = (o.len ?? rnd(20, 36)) * Math.sqrt(S), maxLife = o.life ?? rnd(2.8, 4.2), col = o.col || hex("#fff6d8");
    let x = o.x ?? rnd(W * 0.1, W * 0.9), y = o.y ?? rnd(-5, H * 0.35), life = 0;
    const ux = Math.cos(ang) * dir, uy = Math.sin(ang);
    return { layer: "near", draw(ctx, dt) {
      life += dt; x += ux * sp * dt; y += uy * sp * dt;
      const fade = life < 0.3 ? life / 0.3 : clamp(1 - (life - maxLife * 0.65) / (maxLife * 0.35), 0, 1);
      for (let i = 1; i < len; i++) {
        const a = fade * Math.pow(1 - i / len, 1.6); if (a < 0.03) break;
        ctx.fillStyle = rgba(col, a); ctx.fillRect(Math.round(x - ux * i), Math.round(y - uy * i), 1, 1);
      }
      ctx.fillStyle = rgba(WHITE, fade); ctx.fillRect(Math.round(x), Math.round(y), 1, 1);
      if (o.big) { ctx.fillStyle = rgba(WHITE, fade * 0.5); ctx.fillRect(Math.round(x) + 1, Math.round(y), 1, 1); ctx.fillRect(Math.round(x), Math.round(y) + 1, 1, 1); }
      return life < maxLife && x > -40 && x < W + 40 && y < H + 40;
    } };
  }
  function meteorShower() {
    const n = rnd(18, 30) | 0, dir = Math.random() < 0.5 ? 1 : -1, ang = rnd(0.35, 0.55);   // ~12 s of meteors
    const rx = rnd(W * 0.2, W * 0.8), ry = rnd(-10, H * 0.15), kids = [];
    let spawned = 0, tt = 0, next = 0;
    return { layer: "near", draw(ctx, dt) {
      tt += dt;
      while (spawned < n && tt >= next) {
        kids.push(shootingStar({ dir, ang: ang + rnd(-0.07, 0.07), x: rx + rnd(-60, 60), y: ry + rnd(-20, 20), sp: rnd(100, 150), len: rnd(12, 26), life: rnd(2, 3.2), big: Math.random() < 0.2 }));
        spawned++; next += rnd(0.25, 0.7);
      }
      for (let i = kids.length - 1; i >= 0; i--) if (!kids[i].draw(ctx, dt)) kids.splice(i, 1);
      return spawned < n || kids.length > 0;
    } };
  }
  function supernova() {
    // ~40 s: the star swells (4 s), flares (4 s), throws off a shock ring (10 s), leaves a glowing remnant
    const x = rnd(W * 0.04, W * 0.96) | 0, y = rnd(H * 0.05, H * 0.6) | 0, maxR = rnd(10, 22) * S, star = { size: 1, c: WHITE };
    let tt = 0;
    return { layer: "far", draw(ctx, dt) {
      tt += dt;
      if (tt < 4) { star.size = (1 + (tt / 4) * 2.5) | 0; drawStar(ctx, x, y, star, 0.4 + 0.6 * (tt / 4)); return true; }
      const t2 = tt - 4, ph = t2 / 10;
      if (t2 < 4) {                                                                               // the flare
        const k = 1 - t2 / 4, reach = 7 * S * (1 + 0.35 * Math.sin(t2 * 5)) * (0.5 + 0.5 * k);
        star.size = 4; drawStar(ctx, x, y, star, 1);
        line(ctx, x - reach, y, x + reach, y, WHITE, 0.25 * k + 0.05, 0.25 * k + 0.05);
        line(ctx, x, y - reach, x, y + reach, WHITE, 0.25 * k + 0.05, 0.25 * k + 0.05);
      } else if (t2 < 10) { star.size = 2; drawStar(ctx, x, y, star, 0.7 * (1 - (t2 - 4) / 6)); }
      const rr = maxR * smooth(Math.min(1, t2 / 8));
      const fade = clamp(1 - t2 / 36, 0, 1);
      for (let dy = -rr; dy <= rr; dy++) for (let dx = -rr; dx <= rr; dx++) {                   // remnant
        const d = Math.hypot(dx, dy) / (rr || 1); if (d > 1) continue;
        const px = (x + dx) | 0, py = (y + dy) | 0;
        if (hash2(px, py) < 0.55 * (1 - d) * (0.6 + noise(px * 0.2, py * 0.2, 2, 2))) {
          ctx.fillStyle = rgba(pick(P.nova, 0.2 + 0.5 * (1 - d), px, py), 0.45 * fade * (1 - d * 0.5)); ctx.fillRect(px, py, 1, 1);
        }
      }
      if (ph <= 1) {                                                                            // shock ring
        const n = Math.max(16, (TAU * rr) | 0);
        for (let i = 0; i < n; i++) {
          const a = (i / n) * TAU, px = Math.round(x + Math.cos(a) * rr), py = Math.round(y + Math.sin(a) * rr);
          ctx.fillStyle = rgba(pick(P.nova, 1 - ph, px, py), (1 - ph) * 0.9); ctx.fillRect(px, py, 1, 1);
        }
      }
      return tt < 40;
    } };
  }
  function comet() {
    const fromLeft = Math.random() < 0.5, dur = rnd(24, 36), tail = rnd(22, 40) * S, col = hex("#c4e8ff");
    let x = fromLeft ? -12 : W + 12, y = rnd(H * 0.08, H * 0.5), tt = 0;
    const vx = ((fromLeft ? W + 12 : -12) - x) / dur, vy = rnd(-H * 0.2, H * 0.3) / dur;
    return { layer: "mid", draw(ctx, dt) {
      tt += dt; x += vx * dt; y += vy * dt;
      let ax = x - lay.sun.x, ay = y - lay.sun.y; const al = Math.hypot(ax, ay) || 1; ax /= al; ay /= al;   // tail points away from the sun
      for (let i = 1; i < tail; i++) {
        const k = i / tail, spread = k * 2.5 * S;
        for (let w = -spread; w <= spread; w++) {
          const a = Math.pow(1 - k, 1.3) * 0.55 * (1 - Math.abs(w) / (spread + 1));
          const px = Math.round(x + ax * i - ay * w), py = Math.round(y + ay * i + ax * w);
          if (a > bayer(px, py) * 0.5) { ctx.fillStyle = rgba(col, a); ctx.fillRect(px, py, 1, 1); }
        }
      }
      const hx = Math.round(x), hy = Math.round(y);                        // the head, centred on the tail's start
      ctx.fillStyle = "#ffffff"; ctx.fillRect(hx - 1, hy - 1, 2, 2);
      ctx.fillStyle = rgba(col, 0.4); ctx.fillRect(hx - 2, hy - 1, 1, 2); ctx.fillRect(hx + 1, hy - 1, 1, 2);
      return tt < dur;
    } };
  }
  function asteroid() {
    const R = rnd(3, 6) * S, frames = [];
    for (let k = 0; k < 8; k++) {
      const rot = (k / 8) * TAU, size = Math.ceil(R * 2.6) + 2, c = size / 2;
      frames.push(paint(size, (x, y) => {
        const dx = x - c + 0.5, dy = y - c + 0.5, th = Math.atan2(dy, dx) - rot;
        const edge = R * (0.78 + 0.45 * noise(Math.cos(th) * 1.3 + 5, Math.sin(th) * 1.3, 2, 2));
        const d = Math.hypot(dx, dy); if (d > edge) return null;
        const lum = 0.5 + (-dx * 0.6 - dy * 0.5) / (edge * 1.6);
        return pick(P.rock, lum + (noise((dx * Math.cos(rot) - dy * Math.sin(rot)) * 0.5 + 9, (dx * Math.sin(rot) + dy * Math.cos(rot)) * 0.5, 3, 2) - 0.5) * 0.5, x, y);
      }));
    }
    const fromLeft = Math.random() < 0.5, dur = rnd(18, 28);
    let x = fromLeft ? -10 : W + 10, y = rnd(H * 0.15, H * 0.85), tt = 0;
    const vx = (fromLeft ? W + 20 : -W - 20) / dur, vy = rnd(-H * 0.25, H * 0.25) / dur;
    return { layer: "mid", draw(ctx, dt) {
      tt += dt; x += vx * dt; y += vy * dt;
      const f = frames[((tt / 1.1) | 0) % frames.length];
      ctx.drawImage(f, Math.round(x - f.width / 2), Math.round(y - f.height / 2));
      return tt < dur;
    } };
  }
  function pulsar() {
    const x = rnd(W * 0.05, W * 0.95) | 0, y = rnd(H * 0.05, H * 0.95) | 0, col = hex("#bfe0ff"), len = 7 * S;
    let tt = 0;
    return { layer: "far", draw(ctx, dt) {
      tt += dt;
      const fade = Math.min(1, tt / 1.5, (15 - tt) / 2.5), a = (tt * TAU) / 1.2;
      drawStar(ctx, x, y, { size: 2, c: col }, fade);
      for (const s of [1, -1]) line(ctx, x, y, x + Math.cos(a) * len * s, y + Math.sin(a) * len * s, col, 0.8 * fade, 0);
      return tt < 15;
    } };
  }
  function hyperspace() {
    // ~2 s to spool up and jump, 6-10 s away, ~2 s to drop back in
    const pool = fleet.filter((s) => !s.hidden && s.jumper);
    if (!pool.length) return null;
    const sh = pool[(Math.random() * pool.length) | 0], away = rnd(6, 10), col = hex("#d8f4ff");
    const back = 2.4 + away;
    let tt = 0, from;
    return { layer: "near", draw(ctx, dt, now) {
      tt += dt;
      const [x, y] = shipPos(sh, now), ox = Math.round(x - sh.sp.w / 2), oy = Math.round(y - sh.sp.h / 2);
      const nose = sh.sp.nose || [0, sh.sp.h / 2], nx = ox + nose[0], ny = oy + nose[1];
      if (tt < 1.2) {                                                   // its own engines spool up
        const k = tt / 1.2;
        for (const [ex, ey] of sh.sp.exhaust || []) for (let i = 0; i < 5; i++) {
          ctx.fillStyle = rgba(col, k * (1 - i / 5) * (0.7 + Math.random() * 0.3)); ctx.fillRect(ox + ex + i, oy + ey, 1, 1);
        }
      } else if (tt < 2.4) {                                            // the jump: a streak that lingers
        if (!from) from = [x, y, nx, ny];
        sh.hidden = true;
        const k = smooth(Math.min(1, (tt - 1.2) / 0.35)), fade = 1 - smooth((tt - 1.5) / 0.9), x0 = from[0] - k * W * 0.8;
        line(ctx, from[0] + sh.sp.w / 2, from[1], x0, from[1], col, 0.1 * fade, fade);
        line(ctx, from[0] + sh.sp.w / 2, from[1] - 1, x0 + 10, from[1] - 1, col, 0.05 * fade, 0.45 * fade);
        line(ctx, from[0] + sh.sp.w / 2, from[1] + 1, x0 + 10, from[1] + 1, col, 0.05 * fade, 0.45 * fade);
        if (tt < 1.45) { ctx.fillStyle = rgba(WHITE, 1 - (tt - 1.2) / 0.25); ctx.fillRect(from[2] - 2, from[3] - 2, 5, 5); }   // at the nose
      } else if (tt < back) {
        sh.hidden = true;
      } else if (tt < back + 1.2) {                                     // drop back in from behind
        const k = smooth((tt - back) / 1.2), xs = lerp(W + 20, nx, k);   // streaks in from behind, up to the nose
        line(ctx, W + 20, ny, xs, ny, col, 0, 1);
        line(ctx, W + 20, ny - 1, xs + 8, ny - 1, col, 0, 0.35);
        line(ctx, W + 20, ny + 1, xs + 8, ny + 1, col, 0, 0.35);
      } else {
        sh.hidden = false;
        const k = (tt - back - 1.2) / 0.8;
        if (k >= 1) return false;
        ctx.fillStyle = rgba(WHITE, 1 - k); ctx.fillRect(nx - 1, ny - 1, 3, 3);
      }
      return true;
    } };
  }
  function superlaser() {
    // ~19 s: the dish charges (4 s), the eight tributary beams light one by one (1.6 s), the main beam
    // fires for 10 s with the target burning, then the target blows (3.5 s)
    const [dx, dy] = ds.dish, focus = ds.focus, tribs = ds.emitters;
    const target = [rnd(W * 0.38, W * 0.62), rnd(H * 0.04, H * 0.2)];
    const CHARGE = 4, TRIB = 1.6, FIRE = 10, AFTER = 3.5;
    const t1 = CHARGE, t2 = t1 + TRIB, t3 = t2 + FIRE, end = t3 + AFTER;
    const HOT = ramp("#0f3d17", "#1f8f2e", "#39ff14", "#b9ff9e", "#ffffff");
    let tt = 0;
    return { layer: "near", draw(ctx, dt) {
      tt += dt;
      if (tt < t3) {                                               // the dish fills with green light
        const k = Math.min(1, tt / CHARGE), n = 10 + 22 * k;
        for (let i = 0; i < n; i++) {
          const q = Math.sqrt(Math.random()) * 0.95 * (tt < t2 ? 1 - 0.5 * k * Math.random() : 1);
          const [sx, sy] = ds.onDish(q, Math.random() * TAU);
          ctx.fillStyle = rgba(SABER, 0.25 + 0.6 * k); ctx.fillRect(Math.round(sx), Math.round(sy), 1, 1);
        }
        ctx.fillStyle = rgba(WHITE, 0.4 + 0.5 * k * (0.6 + 0.4 * Math.sin(tt * 9))); ctx.fillRect(Math.round(dx), Math.round(dy), 1, 1);   // the lens
      }
      if (tt >= t1 && tt < t3 + 0.5) {                             // tributary beams, lit in turn
        const lit = Math.min(8, 1 + (((tt - t1) / TRIB) * 8) | 0), f = tt < t3 ? 1 : 1 - (tt - t3) / 0.5;
        for (let i = 0; i < lit; i++) line(ctx, tribs[i][0], tribs[i][1], focus[0], focus[1], SABER, (tt < t2 ? 0.9 : 0.55 + 0.2 * Math.random()) * f, (tt < t2 ? 0.9 : 0.7) * f);
      }
      if (tt >= t2 && tt < t3 + 0.5) {                             // the beam, 10 s
        const f = tt < t3 ? 1 : 1 - (tt - t3) / 0.5, wob = 0.82 + 0.18 * Math.sin(tt * 37) * Math.sin(tt * 11);
        const bx = target[0] - focus[0], by = target[1] - focus[1], bl = Math.hypot(bx, by) || 1, nx = -by / bl, ny = bx / bl;
        for (const o of [-2, 2]) line(ctx, focus[0] + nx * o, focus[1] + ny * o, target[0] + nx * o, target[1] + ny * o, SABER, 0.18 * f, 0.18 * f);
        line(ctx, focus[0], focus[1], target[0], target[1], SABER, f * wob, f * wob, 2);
        line(ctx, focus[0], focus[1], target[0], target[1], WHITE, 0.9 * f * wob, 0.9 * f * wob);
        const burn = smooth((tt - t2) / FIRE), r = 2 + 6 * S * burn;       // the target burns hotter
        for (let yy = -r; yy <= r; yy++) for (let xx = -r; xx <= r; xx++) {
          const d = Math.hypot(xx, yy) / r; if (d > 1) continue;
          const px = Math.round(target[0] + xx), py = Math.round(target[1] + yy);
          if (Math.random() < 0.85 - d * 0.5) { ctx.fillStyle = rgba(pick(HOT, (1 - d) * (0.6 + 0.4 * burn), px, py), f * (1 - d * 0.6)); ctx.fillRect(px, py, 1, 1); }
        }
        for (let i = 0; i < 6; i++) {                                  // sparks off the impact
          const a = Math.random() * TAU, sr = r + Math.random() * 6 * S;
          ctx.fillStyle = rgba(Math.random() < 0.5 ? WHITE : SABER, f * 0.8); ctx.fillRect(Math.round(target[0] + Math.cos(a) * sr), Math.round(target[1] + Math.sin(a) * sr), 1, 1);
        }
      }
      if (tt > t3 - 0.2) {                                         // the blast: a ring and debris
        const k = clamp((tt - t3 + 0.2) / (AFTER + 0.2), 0, 1), r = 6 * S + k * 26 * S, n = Math.max(24, (TAU * r) | 0);
        for (let i = 0; i < n; i++) { const a = (i / n) * TAU; ctx.fillStyle = rgba(mix(WHITE, SABER, k), (1 - k) * 0.9); ctx.fillRect(Math.round(target[0] + Math.cos(a) * r), Math.round(target[1] + Math.sin(a) * r * 0.8), 1, 1); }
        for (let i = 0; i < 20; i++) {
          const a = hash2(i, 7) * TAU, dr = (0.3 + hash2(i, 3) * 0.7) * r;
          ctx.fillStyle = rgba(pick(P.rock, 0.3 + hash2(i, 5) * 0.6, i, 1), 1 - k); ctx.fillRect(Math.round(target[0] + Math.cos(a) * dr), Math.round(target[1] + Math.sin(a) * dr), 1, 1);
        }
        if (k < 0.25) { const fr = 3 + k * 20 * S; ctx.fillStyle = rgba(WHITE, 1 - k * 4); ctx.fillRect(Math.round(target[0] - fr / 2), Math.round(target[1] - fr / 2), Math.round(fr), Math.round(fr)); }
      }
      return tt < end;
    } };
  }
  // The fighters press in on the Death Star for ~12 s: red bolts from their cannons, small explosions on
  // its surface, then they pull back.
  function rebelAttack() {
    const shooters = fleet.filter((sh) => !sh.hidden && sh.fighter);
    if (!shooters.length) return null;
    const bolts = [], booms = [], flashes = [], RED = hex("#ff3b30"), HOT = hex("#ffb199"), FIRE = ramp("#7a1c05", "#e8560f", "#ffb347", "#fff1c9");
    const dur = rnd(10, 14);
    let tt = 0, next = 1.2, shot = 0;
    return { layer: "near", draw(ctx, dt, now) {
      if (!tt) attack = { t0: now, dur };
      tt += dt;
      while (tt < dur - 0.8 && tt >= next) {
        const sh = shooters[(Math.random() * shooters.length) | 0];
        if (!sh.hidden) {
          const [x, y] = shipPos(sh, now), gun = sh.sp.guns[shot++ % sh.sp.guns.length];
          const sx = Math.round(x - sh.sp.w / 2) + gun[0], sy = Math.round(y - sh.sp.h / 2) + gun[1];   // the drawn cannon tip
          const tx = ds.x + rnd(-0.55, 0.55) * ds.r, ty = ds.y + rnd(-0.55, 0.55) * ds.r;
          const d = Math.hypot(tx - sx, ty - sy) || 1;
          bolts.push({ x: sx, y: sy, ux: (tx - sx) / d, uy: (ty - sy) / d, tx, ty, left: d });
          flashes.push({ x: sx, y: sy, t: 0 });
        }
        next += rnd(0.12, 0.45);
      }
      for (let i = bolts.length - 1; i >= 0; i--) {
        const b = bolts[i], step = 220 * dt;
        b.x += b.ux * step; b.y += b.uy * step; b.left -= step;
        if (b.left <= 0) { booms.push({ x: b.tx, y: b.ty, t: 0 }); bolts.splice(i, 1); continue; }
        for (let k = 0; k < 6; k++) { ctx.fillStyle = rgba(k ? RED : HOT, 1 - k * 0.15); ctx.fillRect(Math.round(b.x - b.ux * k), Math.round(b.y - b.uy * k), 1, 1); }
      }
      for (let i = flashes.length - 1; i >= 0; i--) {                      // muzzle flashes
        const f = flashes[i]; f.t += dt;
        if (f.t > 0.12) { flashes.splice(i, 1); continue; }
        ctx.fillStyle = rgba(HOT, 1 - f.t / 0.12); ctx.fillRect(f.x - 1, f.y, 1, 1);
        ctx.fillStyle = rgba(WHITE, 1 - f.t / 0.12); ctx.fillRect(f.x, f.y, 1, 1);
      }
      for (let i = booms.length - 1; i >= 0; i--) {
        const bm = booms[i]; bm.t += dt;
        const k = bm.t / 0.9, r = 1 + k * 5 * S;
        for (let a = 0; a < 12; a++) {
          const ang = a * 0.52 + bm.t * 3;
          ctx.fillStyle = rgba(pick(FIRE, 1 - k, a, i), 1 - k);
          ctx.fillRect(Math.round(bm.x + Math.cos(ang) * r), Math.round(bm.y + Math.sin(ang) * r), 1, 1);
        }
        if (k < 0.4) { ctx.fillStyle = rgba(WHITE, 1 - k * 2); ctx.fillRect(Math.round(bm.x), Math.round(bm.y), 1, 1); }
        if (bm.t > 0.9) booms.splice(i, 1);
      }
      const more = tt < dur + 1.5 || bolts.length > 0 || booms.length > 0 || flashes.length > 0;   // +1.5 s: time to pull back
      if (!more) attack = null;
      return more;
    } };
  }
  // The day's move tilts the odds (set from the dashboard): rebels press the attack on up days, the
  // Death Star charges on down days. mood runs -1 (down 2%+) to +1 (up 2%+).
  let mood = 0;
  const up = () => Math.max(0, mood), down = () => Math.max(0, -mood);
  const EVENTS = {
    rebel_attack: [() => 5 + 16 * up(), rebelAttack],
    shooting_star: [24, () => shootingStar({ big: Math.random() < 0.3 })],
    meteor_shower: [14, meteorShower],
    supernova: [12, supernova],
    comet: [12, comet],
    asteroid: [10, asteroid],
    pulsar: [8, pulsar],
    hyperspace: [() => 12 + 6 * up(), hyperspace],
    superlaser: [() => 4 + 16 * down(), superlaser],
  };
  const weight = (k) => (typeof EVENTS[k][0] === "function" ? EVENTS[k][0]() : EVENTS[k][0]);
  const SOLO = new Set(["rebel_attack", "meteor_shower", "supernova", "hyperspace", "superlaser"]);  // one at a time
  function start(name) {
    if (consumed || BH) return;
    if (name === "blackhole") return startBlackHole();
    const e = EVENTS[name]; if (!e) return;
    if (SOLO.has(name) && events.some((ev) => ev.name === name)) return;
    const ev = e[1](); if (ev) { ev.name = name; events.push(ev); }
  }
  function pickEvent() {
    let total = 0; for (const k in EVENTS) total += weight(k);
    let r = Math.random() * total;
    for (const k in EVENTS) { r -= weight(k); if (r <= 0) return k; }
    return "shooting_star";
  }
  function schedule() {
    clearTimeout(timer);
    if (consumed || reduceMotion) return;
    timer = setTimeout(() => {
      if (!document.hidden && !BH) { if (Math.random() < CFG.odds) startBlackHole(); else start(pickEvent()); }
      schedule();
    }, rnd(CFG.minGap, CFG.maxGap) * 1000);
  }

  // ------------------------------------------------------------------ the black hole
  function startBlackHole() {
    if (BH || consumed) return;
    BH = { x: rnd(W * 0.3, W * 0.7), y: rnd(H * 0.3, H * 0.7), t0: performance.now() / 1000, rh50: 0 };
    events.length = 0;
    clearTimeout(timer);
  }
  function bhState(now) {
    const tb = now - BH.t0, minD = Math.min(W, H), diag = Math.hypot(W, H), base = 0.6 + 4.4 * S;
    let rh, k = 0;
    if (tb < 25) rh = 0.6 + 4.4 * S * smooth(tb / 25);
    else if (tb < 50) { k = smooth((tb - 25) / 25); rh = base + 0.1 * minD * k; }
    else { k = 1; rh = lerp(base + 0.1 * minD, diag * 1.15, smooth((tb - 50) / 14)); }
    return { tb, rh, k, dom: smooth((tb - 38) / 18), overlay: tb > 22, done: tb > 65 };
  }
  // Pull a point into the hole as k -> 1: closer things fall first and swirl faster.
  function warp(x, y, bh) {
    if (!bh || bh.k <= 0) return [x, y, 1];
    const dx = x - BH.x, dy = y - BH.y, d = Math.hypot(dx, dy) + 1e-6;
    const nd = d * Math.pow(1 - bh.k * 0.999, 1 + 40 / (d + 8));
    if (nd < bh.rh) return null;
    const a = Math.atan2(dy, dx) + (bh.k * bh.k * 6) / (1 + d / 60);
    return [BH.x + Math.cos(a) * nd, BH.y + Math.sin(a) * nd, clamp(nd / d, 0.05, 1)];
  }
  let bhScratch = null;
  function drawBlackHole(ctx, bh, now) {
    const rh = bh.rh, outer = rh * 3.4, step = rh > 28 ? 2 : 1;
    const x0 = Math.max(0, Math.floor(BH.x - outer)), x1 = Math.min(W, Math.ceil(BH.x + outer));
    const y0 = Math.max(0, Math.floor(BH.y - Math.max(outer * 0.5, rh * 1.7))), y1 = Math.min(H, Math.ceil(BH.y + Math.max(outer * 0.5, rh * 1.7)));
    const w = x1 - x0, h = y1 - y0; if (w <= 0 || h <= 0) return;
    if (!bhScratch || bhScratch.width < w || bhScratch.height < h) bhScratch = canvas(Math.max(w, 8), Math.max(h, 8));
    const sctx = bhScratch.getContext("2d"), img = sctx.createImageData(w, h), d = img.data;
    const spin = now * 1.8;
    for (let yy = 0; yy < h; yy += step) for (let xx = 0; xx < w; xx += step) {
      const px = x0 + xx + 0.5, py = y0 + yy + 0.5, dx = px - BH.x, dy = py - BH.y, dist = Math.hypot(dx, dy);
      const e = Math.hypot(dx, dy / 0.38) / rh, inDisk = e > 1.25 && e < 3.4;
      let col = null, a = 0;
      const diskCol = () => {
        const heat = 1 - (e - 1.25) / 2.15, ang = Math.atan2(dy / 0.38, dx);
        const t = heat * 0.78 + (0.5 + 0.5 * Math.sin(ang * 4 - spin + e * 5)) * 0.2 + (dx < 0 ? 0.08 : 0);
        return [pick(P.disk, t, x0 + xx, y0 + yy), clamp(heat * 1.3, 0, 1)];
      };
      if (inDisk && dy >= 0) [col, a] = diskCol();                                             // near side, in front
      else if (dist < rh) { col = [0, 0, 0]; a = 1; }                                          // event horizon
      else if (Math.abs(dist - rh * 1.12) < 0.7 + rh * 0.03) { col = hex("#ffe9c4"); a = 0.95; } // photon ring
      else if (dy < 0 && dist < rh * 1.7) { const h2 = 1 - (dist - rh * 1.15) / (rh * 0.55); col = pick(P.disk, 0.5 + h2 * 0.4, x0 + xx, y0 + yy); a = clamp(h2, 0, 1) * 0.8; } // lensed far side
      else if (inDisk) [col, a] = diskCol();
      if (!col || a <= 0) continue;
      for (let sy = 0; sy < step && yy + sy < h; sy++) for (let sx = 0; sx < step && xx + sx < w; sx++) {
        const i = ((yy + sy) * w + xx + sx) * 4; d[i] = col[0]; d[i + 1] = col[1]; d[i + 2] = col[2]; d[i + 3] = (a * 255) | 0;
      }
    }
    sctx.clearRect(0, 0, bhScratch.width, bhScratch.height);
    sctx.putImageData(img, 0, 0);
    ctx.drawImage(bhScratch, 0, 0, w, h, x0, y0, w, h);
  }
  // The dashboard spirals into the hole. Transform + opacity only: both stay on the GPU (a blur filter
  // over the whole page stalls the browser).
  // (Transform .stApp, not #root: a transform on #root turns it into the containing block of the
  // absolutely positioned app, which then collapses to zero height.)
  function swallowPage(s) {
    const root = document.querySelector(".stApp") || document.getElementById("root"); if (!root) return;
    root.style.willChange = "transform, opacity";
    root.style.transformOrigin = BH.x * PIX + "px " + BH.y * PIX + "px";
    root.style.transform = "rotate(" + (s * s * 540).toFixed(1) + "deg) scale(" + Math.max(0.001, 1 - s).toFixed(4) + ")";
    root.style.opacity = (1 - s * s * 0.7).toFixed(3);
  }
  function consume() {
    consumed = true; api.consumed = true;
    cancelAnimationFrame(raf); clearTimeout(timer);
    fxC.style.display = "block"; fxC.style.pointerEvents = "auto"; fxC.style.width = "100vw"; fxC.style.height = "100vh"; fxC.style.background = "#000";
    fg.fillStyle = "#000"; fg.fillRect(0, 0, fxC.width, fxC.height);
    document.documentElement.classList.add("space-consumed");
  }

  // ------------------------------------------------------------------ frame
  function draw(now, dt) {
    const t = Date.now() / 1000;
    const bh = BH ? bhState(now) : null;
    if (bh && bh.done) return consume();
    const pulling = bh && bh.k > 0;

    // re-render one stale planet sprite per frame (their light and spin change slowly)
    const stale = PL.concat(far).find((p) => performance.now() - p.rendered > (p.id in TEX && far.includes(p) ? 3000 : 700));
    if (stale) (far.includes(stale) ? renderFar : renderPlanet)(stale, t);

    if (pulling) {
      g.fillStyle = "#03040a"; g.fillRect(0, 0, W, H);
      g.globalAlpha = 1 - bh.k; g.drawImage(neb, 0, 0); g.globalAlpha = 1;
      for (const s of stars) { const w = warp(s.x, s.y, bh); if (w) drawStar(g, w[0], w[1], s, s.a * (s.tw ? twinkle(s, now) : 1)); }
    } else {
      g.drawImage(neb, 0, 0); g.drawImage(starsStatic, 0, 0);
      for (const s of twinklers) drawStar(g, s.x, s.y, s, s.a * (0.35 + 0.65 * twinkle(s, now)));
    }
    drawFar(g, t, now, bh);
    const run = (layer) => { for (let i = events.length - 1; i >= 0; i--) if (events[i].layer === layer && !events[i].draw(g, dt, now)) events.splice(i, 1); };
    run("far");

    g.globalAlpha = pulling ? 1 - bh.k : 1; g.drawImage(orbitsC, 0, 0); g.globalAlpha = 1;
    const order = PL.map((p) => [p, planetPos(p, t).z]);
    order.filter(([, z]) => z < 0).forEach(([p]) => drawPlanet(g, p, t, bh, now));
    let sx = lay.sun.x, sy = lay.sun.y, ss = 1;
    if (bh) { const w = warp(sx, sy, bh); if (w) [sx, sy, ss] = w; else ss = 0; }
    if (ss > 0) {
      const sz = Math.max(1, Math.round(sunSprite.c.width * ss));
      g.globalAlpha = 0.92 + 0.08 * Math.sin(now * 0.7);
      g.drawImage(sunSprite.c, Math.round(sx - sz / 2), Math.round(sy - sz / 2), sz, sz);
      g.globalAlpha = 1;
    }
    order.filter(([, z]) => z >= 0).forEach(([p]) => drawPlanet(g, p, t, bh, now));

    run("mid");
    drawDeathStar(g, now, bh);
    drawFleet(g, now, bh);
    run("near");

    if (bh) {
      if (bh.overlay) {
        fxC.style.display = "block";
        fg.clearRect(0, 0, W, H);
        drawBlackHole(fg, bh, now);
      } else drawBlackHole(g, bh, now);
      if (bh.dom > 0) swallowPage(bh.dom);
    }
  }
  function frame(ts) {
    raf = requestAnimationFrame(frame);
    if (ts - last < 33) return;                                   // ~30 fps is plenty for this
    const dt = Math.min(0.1, (ts - last) / 1000 || 0.033);
    last = ts;
    try { draw(ts / 1000, dt); } catch (e) { console.error("[space]", e); }
  }

  // ------------------------------------------------------------------ layout + api
  function layout() {
    const vw = window.innerWidth, vh = window.innerHeight;
    W = Math.ceil(vw / PIX); H = Math.ceil(vh / PIX);
    for (const c of [bgC, fxC]) { c.width = W; c.height = H; c.style.width = W * PIX + "px"; c.style.height = H * PIX + "px"; }
    g.imageSmoothingEnabled = false; fg.imageSmoothingEnabled = false;
    S = clamp(H / 300, 0.75, 1.7);
    // The Death Star and the fleet sit in the gutters the content leaves free (see base.css max-width).
    const content = vw >= 1000 ? clamp(vw * 0.66, 900, 1400) : vw;
    const gutter = Math.max(0, (vw - content) / 2) / PIX;
    const r = Math.round(clamp(gutter > 20 ? Math.min(H * 0.13, gutter * 0.42) : H * 0.1, 14, 60));
    lay = {
      sun: { x: W / 2, y: H * 0.52 },
      orbit: { rx: W * 0.47, tilt: 0.3 },
      ds: { x: Math.round(gutter > 20 ? gutter * 0.5 : W * 0.14), y: Math.round(H * 0.42), r },
      fleetX: gutter > 20 ? W - gutter * 0.5 : W * 0.84,
    };
    buildSky(); buildSun(); buildOrbits(); buildDeathStar(); buildFleet(); buildFar();
    PL.forEach((p) => { p.rendered = -1; renderPlanet(p, Date.now() / 1000); });
    far.forEach((f) => renderFar(f, Date.now() / 1000));
  }
  function configure(cfg) {
    CFG = cfg;
    document.documentElement.setAttribute("data-space-style", cfg.style);
    if (cfg.force && !forced[cfg.force]) { forced[cfg.force] = true; setTimeout(() => start(cfg.force), 1500); }
  }
  const setMood = (m) => { mood = clamp(Number(m) || 0, -1, 1); };
  const api = { alive: true, consumed: false, configure, setMood, trigger: start, events: Object.keys(EVENTS).concat("blackhole"),
    active: () => events.map((e) => e.name),
    deathStar: () => ({ x: ds.x, y: ds.y, r: ds.r, dish: ds.dish, focus: ds.focus, emitters: ds.emitters }) };
  window.__space = api;

  layout();
  let resizeT = 0;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (!BH && !consumed) layout(); }, 250); });
  raf = requestAnimationFrame(frame);
  schedule();
  configure(CFG);
})();
