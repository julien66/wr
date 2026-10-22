// Traduction : window.I18N = {lang, t} est fourni par la page ; les textes français sont dans le code, t les remplace.
var I18N = window.I18N || { lang: "fr", t: {} };
function T(key, fallback, vars) {
  var s = I18N.t[key] || fallback;
  if (vars) for (var k in vars) s = s.split("{" + k + "}").join(vars[k]);
  return s;
}
function fmtInt(v) {
  var L = I18N.lang, s = String(v);
  if (L === "en") return s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (L === "fr") return s.replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f");
  if (L === "es" && Math.abs(v) < 10000) return s;
  return s.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}
function fmtDec(x, d) { var s = x.toFixed(d); return I18N.lang === "en" ? s : s.replace(".", ","); }
function pctSuf() { return I18N.lang === "en" ? "%" : " %"; }
function ordinal(n) {
  var L = I18N.lang;
  if (L === "fr") return n === 1 ? "1ᵉʳ" : fmtInt(n) + "ᵉ";
  if (L === "de") return fmtInt(n) + ".";
  if (L === "es") return fmtInt(n) + ".º";
  var r = n % 100, u = n % 10, suf = r >= 11 && r <= 13 ? "th" : u === 1 ? "st" : u === 2 ? "nd" : u === 3 ? "rd" : "th";
  return fmtInt(n) + suf;
}

// Filtre de recherche et masquage des inactifs. Tout fonctionne sans JavaScript (le tableau complet reste lisible).
(function () {
  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }
  };

  document.querySelectorAll("[data-filter]").forEach(function (input) {
    var rows = null;
    input.addEventListener("input", function () {
      rows = rows || document.querySelectorAll(input.dataset.filter);
      var q = input.value.trim().toLowerCase();
      rows.forEach(function (r) {
        r.style.display = (!q || r.textContent.toLowerCase().indexOf(q) !== -1) ? "" : "none";
      });
    });
  });

  var toggle = document.getElementById("hide-off");
  var board = document.getElementById("board");
  if (toggle && board) {
    var apply = function () { board.classList.toggle("hide-off", toggle.checked); };
    toggle.checked = store.get("hideOff") === "1";
    apply();
    toggle.addEventListener("change", function () { store.set("hideOff", toggle.checked ? "1" : "0"); apply(); });
  }

  // graphiques plus larges que l'écran (mobile) : on montre d'abord la partie la plus récente
  document.querySelectorAll(".chartscroll").forEach(function (el) { el.scrollLeft = el.scrollWidth; });

  // listes déroulantes qui s'appliquent tout de suite (pays du classement)
  document.querySelectorAll("select[data-autosubmit]").forEach(function (sel) {
    sel.addEventListener("change", function () { sel.form.submit(); });
  });

  // Liste des compétitions : années repliables, filtres texte / type / taille du plateau.
  var list = document.getElementById("list"), cq = document.getElementById("cq");
  if (list && cq) {
    var kind = document.getElementById("ckind"), size = document.getElementById("csize"), count = document.getElementById("ccount");
    var blocks = Array.prototype.slice.call(list.querySelectorAll("details.yblock"));
    var initOpen = blocks.map(function (d) { return d.open; });
    var openHash = function () {
      var d = location.hash && document.getElementById(location.hash.slice(1));
      if (d && d.tagName === "DETAILS") { d.open = true; d.scrollIntoView(); }
    };
    window.addEventListener("hashchange", openHash);
    openHash();
    var filter = function () {
      var q = cq.value.trim().toLowerCase(), k = kind.value, n = +size.value, any = q || k || n, shown = 0;
      blocks.forEach(function (d, i) {
        var hits = 0;
        d.querySelectorAll("li").forEach(function (li) {
          var ok = (!q || li.textContent.toLowerCase().indexOf(q) !== -1) && (!k || li.dataset.kind === k) && (+li.dataset.n >= n);
          li.style.display = ok ? "" : "none";
          if (ok) hits++;
        });
        shown += hits;
        d.style.display = hits ? "" : "none";
        d.open = any ? hits > 0 : initOpen[i];
      });
      count.hidden = !any;
      count.textContent = T("js.ncomps", "{n} compétition(s)", { n: fmtInt(shown) });
    };
    [cq, kind, size].forEach(function (el) { el.addEventListener(el === cq ? "input" : "change", filter); });
  }

  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.picker[open], details.langsw[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
})();

// Graphique d'évolution d'un pilote : un point par compétition, courbe en escalier (la valeur ne change qu'en compétition).
(function () {
  var host = document.getElementById("pchart"), raw = document.getElementById("pdata"), tip = document.getElementById("ptip");
  if (!host || !raw) return;
  var D = JSON.parse(raw.textContent), P = D.pts, NS = "http://www.w3.org/2000/svg";
  if (!P.length) return;
  var rawR = document.getElementById("rdata"), RD = rawR ? JSON.parse(rawR.textContent) : null;
  var DAY = 864e5, MONTH = 30.4375 * DAY;
  var fr = fmtInt;
  var RX = RD ? RD.pts.map(function (p) { return Date.UTC(+p.m.slice(0, 4), +p.m.slice(5, 7), 0); }) : [];   // fin de mois
  var byMonth = {}; if (RD) RD.pts.forEach(function (p) { byMonth[p.m] = p; });
  var rBest = -1; if (RD) RD.pts.forEach(function (p, i) { if (p.r && (rBest < 0 || p.r < RD.pts[rBest].r)) rBest = i; });   // mois inactifs : pas de rang
  var showF = !!(RD && RD.fem && RD.pts.some(function (p) { return p.f; }));
  if (showF) { var kf = document.querySelector(".key .kf"); if (kf) kf.hidden = false; }
  var RTICKS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000];
  var rmax = RD ? Math.max.apply(null, RD.pts.map(function (p) { return p.r; })) : 2;
  var rbot = RTICKS.filter(function (v) { return v >= rmax; })[0] || rmax; if (rbot < 2) rbot = 2;
  var t = function (iso) { return Date.parse(iso + "T00:00:00Z"); };
  var f1 = function (v) { return fmtDec(v, 1).replace("-", "−"); };
  var rt = function (m, s) { return m - D.z * s; };
  var X = P.map(function (p) { return t(p.d); });
  var R = P.map(function (p) { return rt(p.m1, p.s1); }), M = P.map(function (p) { return p.m1; });
  var t0 = Math.min(X[0], RD ? RX[0] : X[0]), t1 = Math.max(X[X.length - 1], t(D.now));
  if (t1 - t0 < 90 * DAY) t1 = t0 + 90 * DAY;
  var lo = Math.min.apply(null, R), hi = Math.max.apply(null, M);
  var pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  var step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000].filter(function (s) { return (hi - lo) / s <= 6; })[0] || 1000;
  var yMin = Math.floor(lo / step) * step, yMax = Math.ceil(hi / step) * step;
  var sel = -1, svg, W, H, m = { l: 38, r: RD ? 50 : 12, t: 10, b: 24 };

  function el(n, a, parent) {
    var e = document.createElementNS(NS, n);
    for (var k in a) e.setAttribute(k, a[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  var sx = function (v) { return m.l + (v - t0) / (t1 - t0) * (W - m.l - m.r); };
  var sy = function (v) { return m.t + (yMax - v) / (yMax - yMin) * (H - m.t - m.b); };
  var sr = function (r) { return m.t + Math.log(r) / Math.log(rbot) * (H - m.t - m.b); };      // rang : 1 en haut, échelle log
  function rline(key) {
    var d = "", pen = false;
    RD.pts.forEach(function (p, i) { var v = p[key]; if (!v) { pen = false; return; } d += (pen ? "L" : "M") + sx(RX[i]).toFixed(1) + " " + sr(v).toFixed(1); pen = true; });
    return d;
  }
  function path(vals, extend) {
    var d = "M" + sx(X[0]).toFixed(1) + " " + sy(vals[0]).toFixed(1);
    for (var i = 1; i < X.length; i++) d += "H" + sx(X[i]).toFixed(1) + "V" + sy(vals[i]).toFixed(1);
    return d + "H" + sx(t1).toFixed(1);
  }
  function draw() {
    host.textContent = "";
    W = host.clientWidth; H = host.clientHeight;
    svg = el("svg", { viewBox: "0 0 " + W + " " + H, "aria-hidden": "true" }, host);
    for (var v = yMin; v <= yMax + 1e-9; v += step) {
      el("line", { "class": "grid", x1: m.l, x2: W - m.r, y1: sy(v), y2: sy(v) }, svg);
      var tx = el("text", { x: m.l - 6, y: sy(v) + 4, "text-anchor": "end" }, svg); tx.textContent = String(v).replace("-", "−");
    }
    var y0 = new Date(t0).getUTCFullYear(), y1 = new Date(t1).getUTCFullYear(), every = Math.ceil((y1 - y0 + 1) / Math.max(2, Math.floor((W - m.l) / 70)));
    for (var y = y0 + 1; y <= y1; y += every) {
      var xx = sx(Date.UTC(y, 0, 1)); if (xx < m.l + 14 || xx > W - m.r - 14) continue;
      var lt = el("text", { x: xx, y: H - 6, "text-anchor": "middle" }, svg); lt.textContent = y;
    }
    // zones d'inactivité : plus de N mois sans compétition
    var lim = D.inactive * MONTH;
    for (var i = 0; i < X.length; i++) {
      var from = X[i] + lim, to = i + 1 < X.length ? X[i + 1] : t1;
      if (to > from) el("rect", { "class": "offz", x: sx(from), y: m.t, width: Math.max(0, sx(to) - sx(from)), height: H - m.t - m.b }, svg);
    }
    // bande entre rating et mu
    var n = X.length, wash = path(M) + "V" + sy(R[n - 1]);
    for (i = n - 1; i >= 1; i--) wash += "H" + sx(X[i]) + "V" + sy(R[i - 1]);
    if (!D.elo) {
      el("path", { "class": "wash", d: wash + "H" + sx(X[0]) + "Z" }, svg);
      el("path", { "class": "lm", d: path(M) }, svg);
    }
    if (RD) {
      RTICKS.filter(function (v) { return v <= rbot; }).forEach(function (v) {
        var tr = el("text", { "class": "rk-tick", x: W - m.r + 6, y: sr(v) + 4, "text-anchor": "start" }, svg); tr.textContent = fr(v);
      });
      var ax = el("text", { "class": "rk-tick", x: W - m.r + 6, y: m.t - 3, "text-anchor": "start" }, svg); ax.textContent = T("js.rank_axis", "Rang");
      if (showF) el("path", { "class": "rk-f", d: rline("f") }, svg);
      el("path", { "class": "rk", d: rline("r") }, svg);
    }
    el("path", { "class": "ln", d: path(R) }, svg);
    if (RD && rBest >= 0) {
      var bp = RD.pts[rBest], bx = sx(RX[rBest]), by = sr(bp.r);
      var bc = el("circle", { "class": "best", cx: bx, cy: by, r: 6 }, svg);
      var tt = document.createElementNS(NS, "title"); tt.textContent = T("js.best_rank_at", "Meilleur rang : {r}, {m}", { r: ordinal(bp.r), m: new Date(RX[rBest] + DAY).toLocaleDateString(I18N.lang, { month: "long", year: "numeric", timeZone: "UTC" }) }); bc.appendChild(tt);
      var left = bx > W - 170;
      var lab = el("text", { "class": "bestlbl", x: bx + (left ? -10 : 10), y: by - 10, "text-anchor": left ? "end" : "start" }, svg);
      lab.textContent = T("js.best_rank", "Meilleur rang : {r}", { r: ordinal(bp.r) });
    }
    var cross = el("line", { "class": "cross", y1: m.t, y2: H - m.b, visibility: "hidden" }, svg);
    var dots = X.map(function (x, i) { return el("circle", { "class": "dot", cx: sx(x), cy: sy(R[i]), r: 4 }, svg); });
    svg._cross = cross; svg._dots = dots;
    if (sel >= 0) show(sel, false);
  }
  function show(i, move) {
    sel = i;
    svg._dots.forEach(function (d, k) { d.classList.toggle("on", k === i); });
    svg._cross.setAttribute("x1", sx(X[i])); svg._cross.setAttribute("x2", sx(X[i])); svg._cross.setAttribute("visibility", "visible");
    var p = P[i];
    tip.textContent = "";
    var b = document.createElement("b"); b.textContent = p.n; tip.appendChild(b);
    [new Date(X[i]).toLocaleDateString(I18N.lang, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }),
     (D.elo ? T("js.rank_duels", "Rang {k} sur {n} : {w} des duels gagnés (attendu {e})", { k: p.k, n: p.N, w: Math.round(p.w * 100) + pctSuf(), e: Math.round(p.e * 100) + pctSuf() })
            : "Rang " + p.k + " sur " + p.N + " (attendu : " + p.x + ")"),
     (D.elo ? T("js.level", "Niveau {a} → {b}", { a: Math.round(p.m0), b: Math.round(p.m1) })
            : "Rating " + f1(rt(p.m0, p.s0)) + " → " + f1(R[i])),
     (D.elo ? T("js.delta", "Écart {d}", { d: (p.m1 - p.m0 >= 0 ? "+" : "−") + fmtDec(Math.abs(p.m1 - p.m0), 1) })
            : "μ " + f1(p.m1) + ", σ " + p.s1.toFixed(2).replace(".", ",")),
     (function () { var q = byMonth[p.d.slice(0, 7)]; return q && !q.r ? T("js.month_unranked", "Inactif en fin de mois : sans rang") : q ? T("js.month_rank", "Rang mondial en fin de mois : {r} sur {n}", { r: fr(q.r), n: fr(q.n) }) + (q.f ? " · " + T("js.female_rank", "féminin : {r}", { r: fr(q.f) }) : "") : null; })()].forEach(function (line) {
      if (!line) return;
      var s = document.createElement("span"); s.textContent = line; tip.appendChild(s);
    });
    tip.hidden = false;
    var box = host.getBoundingClientRect(), par = tip.offsetParent.getBoundingClientRect();
    var left = box.left - par.left + sx(X[i]) + 14;
    if (left + tip.offsetWidth > par.width - 8) left = box.left - par.left + sx(X[i]) - tip.offsetWidth - 14;
    tip.style.left = Math.max(8, left) + "px"; tip.style.top = (box.top - par.top + 8) + "px";
  }
  function hide() { sel = -1; tip.hidden = true; if (svg) { svg._cross.setAttribute("visibility", "hidden"); svg._dots.forEach(function (d) { d.classList.remove("on"); }); } }
  function nearest(ev) {
    var r = host.getBoundingClientRect(), px = ev.clientX - r.left, best = 0, bd = 1e9;
    X.forEach(function (x, i) { var d = Math.abs(sx(x) - px); if (d < bd) { bd = d; best = i; } });
    return best;
  }
  host.tabIndex = 0;
  host.addEventListener("pointermove", function (e) { show(nearest(e), true); });
  host.addEventListener("pointerdown", function (e) { show(nearest(e), true); });
  host.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse") hide(); });
  host.addEventListener("keydown", function (e) {
    var k = e.key;
    if (k === "ArrowLeft" || k === "ArrowRight" || k === "Home" || k === "End") {
      e.preventDefault();
      var i = sel < 0 ? X.length - 1 : sel;
      i = k === "Home" ? 0 : k === "End" ? X.length - 1 : Math.max(0, Math.min(X.length - 1, i + (k === "ArrowRight" ? 1 : -1)));
      show(i, true);
    } else if (k === "Escape") hide();
  });
  host.addEventListener("blur", hide);
  var w0 = 0;
  window.addEventListener("resize", function () { if (host.clientWidth !== w0) { w0 = host.clientWidth; draw(); } });
  w0 = host.clientWidth; draw();
})();

// Suggestions de pilotes sous la barre de recherche (site dynamique) : flèches + Entrée, Échap pour fermer.
(function () {
  document.querySelectorAll("form.gsearch input[type=search], input[data-pick]").forEach(function (input) {
    var pick = input.dataset.pick && input.form && input.form.elements[input.dataset.pick];
    var box = document.createElement("ul"), items = [], sel = -1, timer = null, seq = 0;
    box.className = "suggest"; box.setAttribute("role", "listbox"); box.hidden = true;
    input.setAttribute("aria-autocomplete", "list");
    input.parentNode.style.position = "relative";
    input.parentNode.appendChild(box);
    function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
    function show(data, total) {
      var list = data; items = list; sel = -1;
      if (!list.length) { box.hidden = true; return; }
      box.innerHTML = list.map(function (p, i) {
        var meta = [p.nation, p.f ? "F" : "", p.years ? (p.years[0] === p.years[1] ? p.years[0] : p.years[0] + "–" + p.years[1]) : "", p.n ? T("js.comps_short", "{n} compét.", { n: p.n }) : ""].filter(Boolean).join(" · ");
        var r = p.active === null ? "<small>" + T("js.unranked", "hors classement") + "</small>"
          : p.rank ? "<b>" + p.rank + "</b><small>" + p.rating + "</small>"
          : "<b>–</b><small>" + T("js.inactive", "inactif") + " · " + p.rating + "</small>";
        return '<li role="option" data-i="' + i + '"><a href="/pilot/' + p.id + '"><span class="sn">' + esc(p.name) + '<small>' + esc(meta) + '</small></span><span class="sr2">' + r + "</span></a></li>";
      }).join("") + (pick ? "" : '<li class="all"><a href="/search?q=' + encodeURIComponent(input.value) + '">' + (total > list.length ? T("js.see_all", "Voir les {n} résultats", { n: total }) : T("js.all_results", "Tous les résultats")) + '…</a></li>');
      box.hidden = false;
    }
    function choose(i) {
      if (!pick) { window.location = "/pilot/" + items[i].id; return; }
      pick.value = items[i].id; input.value = items[i].name; box.hidden = true;
      var f = input.form, other = Array.prototype.filter.call(f.querySelectorAll("input[data-pick]"), function (x) { return x !== input; })[0];
      if (other && f.elements[other.dataset.pick].value) f.submit(); else if (other) other.focus();
    }
    if (pick) box.addEventListener("click", function (e) {
      var li = e.target.closest("li[data-i]");
      if (li) { e.preventDefault(); choose(+li.dataset.i); }
    });
    function mark() { box.querySelectorAll("li[data-i]").forEach(function (li, i) { li.classList.toggle("on", i === sel); }); }
    input.addEventListener("input", function () {
      if (pick) pick.value = "";
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { box.hidden = true; return; }
      timer = setTimeout(function () {
        var my = ++seq;
        fetch("/api/suggest?q=" + encodeURIComponent(q)).then(function (r) { return r.json(); })
          .then(function (d) { if (my === seq) show(d.items, d.total); }).catch(function () {});
      }, 120);
    });
    input.addEventListener("keydown", function (e) {
      if (box.hidden) return;
      if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); mark(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(-1, sel - 1); mark(); e.preventDefault(); }
      else if (e.key === "Enter" && sel >= 0) { choose(sel); e.preventDefault(); }
      else if (e.key === "Escape") { box.hidden = true; }
    });
    document.addEventListener("click", function (e) { if (!input.parentNode.contains(e.target)) box.hidden = true; });
  });
})();
