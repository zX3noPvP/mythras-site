// Mythras AntiCheat site interactions. No dependencies.
(function () {
  "use strict";

  var root = document.documentElement;
  root.classList.add("js");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  // UI strings in the visitor's language, rendered by the server on <body data-i18n-*>
  function tr(key, fallback) { return document.body.getAttribute("data-i18n-" + key) || fallback; }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function pad(n, len) { return String(n).padStart(len || 2, "0"); }

  /* ---------- toast ---------- */
  var toastTimer = null;
  function toast(message) {
    var el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 1800);
  }

  /* ---------- mobile navigation ---------- */
  var navToggle = $(".nav-toggle");
  var navLinks = $("#site-nav");
  if (navToggle && navLinks) {
    function setNav(open) {
      navToggle.setAttribute("aria-expanded", String(open));
      navToggle.setAttribute("aria-label", open ? tr("close-menu", "Close menu") : tr("open-menu", "Open menu"));
      navLinks.classList.toggle("is-open", open);
    }
    navToggle.addEventListener("click", function () {
      setNav(navToggle.getAttribute("aria-expanded") !== "true");
    });
    navLinks.addEventListener("click", function (e) {
      if (e.target.closest("a")) setNav(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && navToggle.getAttribute("aria-expanded") === "true") {
        setNav(false);
        navToggle.focus();
      }
    });
  }

  /* ---------- scroll reveal with a short stagger ---------- */
  var reveals = $$(".reveal");
  reveals.forEach(function (el) {
    var siblings = $$(":scope > .reveal", el.parentElement);
    var i = Math.min(siblings.indexOf(el), 6);
    if (i > 0) el.style.transitionDelay = (i * 60) + "ms";
  });
  if (!reduceMotion && "IntersectionObserver" in window) {
    var revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          revealObserver.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    reveals.forEach(function (el) { revealObserver.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add("in"); });
  }

  /* ---------- hero detection feed (simulated) ---------- */
  var log = $("#console-log");
  if (log) {
    var script = [
      ["CHECK", "check", "Speed · Player_07 · 14.2 b/s"],
      ["AI", "veto", "Player_07 · ping spike 380 ms → vetoed"],
      ["CHECK", "check", "Reach · Player_12 · 3.92 blocks"],
      ["AI", "confirm", "Player_12 · confirmed cheat"],
      ["REPLAY", "replay", "Clip #{id} saved · 12 s"],
      ["CHECK", "check", "Fly · Player_03 · airtime 2.1 s"],
      ["AI", "veto", "Player_03 · elytra plugin → vetoed"],
      ["CHECK", "check", "KillAura · Player_21 · 38 rot/s"],
      ["AI", "confirm", "Player_21 · confirmed cheat"],
      ["REPLAY", "replay", "Clip #{id} saved · 12 s"],
      ["CHECK", "check", "Timer · Player_09 · +18% packets"],
      ["AI", "veto", "Player_09 · server TPS 14 → vetoed"]
    ];
    var counters = { check: 0, veto: 0, confirm: 0 };
    var step = 0;
    var clipId = 4820;
    var clock = new Date();

    function addLine() {
      var item = script[step % script.length];
      step++;
      clock = new Date(clock.getTime() + 1000 + Math.floor(Math.random() * 2500));
      var text = item[2];
      if (item[1] === "replay") { clipId++; text = text.replace("{id}", clipId); }
      if (counters[item[1]] !== undefined) counters[item[1]]++;

      var li = document.createElement("li");
      li.className = "log-line is-" + item[1];
      var t = document.createElement("span");
      t.className = "t";
      t.textContent = pad(clock.getHours()) + ":" + pad(clock.getMinutes()) + ":" + pad(clock.getSeconds());
      var tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = item[0];
      var msg = document.createElement("span");
      msg.className = "msg";
      msg.textContent = text;
      li.appendChild(t); li.appendChild(tag); li.appendChild(msg);
      log.appendChild(li);
      while (log.children.length > 14) log.removeChild(log.firstChild);

      $("#stat-checks").textContent = counters.check;
      $("#stat-vetoed").textContent = counters.veto;
      $("#stat-confirmed").textContent = counters.confirm;
    }

    var initial = reduceMotion ? 12 : 5;
    for (var i = 0; i < initial; i++) addLine();
    if (!reduceMotion) {
      setInterval(function () { if (!document.hidden) addLine(); }, 1400);
    }
  }

  /* ---------- replay video, driven by the custom controls ---------- */
  var player = $("#replay-player");
  var video = $("#rp-video");
  if (player && video) {
    var FLAG_T = 7.4;
    var scrub = $("#rp-scrub", player);
    var timeOut = $("#rp-time", player);
    var playBtn = $("#rp-play", player);
    var events = $$("#replay-events li", player);
    var rafId = null;

    function duration() { return isFinite(video.duration) && video.duration > 0 ? video.duration : 12; }

    function render() {
      var t = video.currentTime;
      var d = duration();
      scrub.value = String(Math.round((t / d) * 1000));
      timeOut.textContent = pad(t.toFixed(1), 4) + " / " + d.toFixed(1);
      var current = -1;
      events.forEach(function (li, i) {
        var past = t + 0.05 >= Number(li.querySelector("button").dataset.t);
        li.classList.toggle("is-past", past);
        if (past) current = i;
      });
      events.forEach(function (li, i) { li.classList.toggle("is-current", i === current); });
    }

    // smooth UI updates while playing (timeupdate alone fires only ~4 times a second)
    function loop() {
      render();
      rafId = video.paused ? null : requestAnimationFrame(loop);
    }

    function syncState() {
      var playing = !video.paused && !video.ended;
      player.classList.toggle("is-playing", playing);
      playBtn.setAttribute("aria-label", playing ? (playBtn.dataset.labelPause || "Pause") : (playBtn.dataset.labelPlay || "Play"));
      if (playing && rafId === null) rafId = requestAnimationFrame(loop);
      render();
    }

    function seek(time) {
      video.currentTime = Math.max(0, Math.min(duration(), time));
      render();
    }

    video.addEventListener("loadedmetadata", function () {
      $("#rp-flag-mark", player).style.left = "calc(8px + (100% - 16px) * " + (FLAG_T / duration()) + ")";
      render();
    });
    ["play", "pause", "ended", "seeked"].forEach(function (ev) { video.addEventListener(ev, syncState); });

    playBtn.addEventListener("click", function () {
      if (video.paused || video.ended) {
        if (video.ended) video.currentTime = 0;
        video.play().catch(function () {});
      } else {
        video.pause();
      }
    });
    video.addEventListener("click", function () { playBtn.click(); });
    $("#rp-back", player).addEventListener("click", function () { seek(video.currentTime - 0.5); });
    $("#rp-fwd", player).addEventListener("click", function () { seek(video.currentTime + 0.5); });
    scrub.addEventListener("input", function () { seek((Number(scrub.value) / 1000) * duration()); });
    events.forEach(function (li) {
      li.querySelector("button").addEventListener("click", function () {
        seek(Number(this.dataset.t));
      });
    });
    $$(".speed button", player).forEach(function (btn) {
      btn.addEventListener("click", function () {
        video.playbackRate = Number(btn.dataset.speed);
        $$(".speed button", player).forEach(function (b) { b.setAttribute("aria-pressed", String(b === btn)); });
      });
    });

    $("#rp-flag-mark", player).style.left = "calc(8px + (100% - 16px) * " + (FLAG_T / 12) + ")";
    render();

    // start playing (muted) once the player scrolls into view; never for reduced motion
    if (!reduceMotion && "IntersectionObserver" in window) {
      var started = false;
      new IntersectionObserver(function (entries, obs) {
        if (!started && entries[0].isIntersecting) {
          started = true;
          obs.disconnect();
          video.play().catch(function () {});
        }
      }, { threshold: 0.3 }).observe(player);
    }
  }

  /* ---------- AI Learning comparison ---------- */
  var aiList = $("#ai-list");
  if (aiList) {
    var items = $$("li", aiList);
    var aiButtons = $$(".ai-toggle button");
    function setMode(mode) {
      aiList.dataset.mode = mode;
      aiButtons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === mode)); });
      var alerts = 0, falses = 0;
      items.forEach(function (li) {
        var real = li.dataset.real === "true";
        var verdict = li.querySelector(".verdict");
        if (mode === "rules") {
          verdict.textContent = tr("alert", "Alert");
          alerts++;
          if (!real) falses++;
        } else {
          verdict.textContent = real ? tr("confirmed", "Confirmed") : tr("vetoed", "Vetoed");
          if (real) alerts++;
        }
      });
      $("#ai-alerts").textContent = alerts;
      $("#ai-false").textContent = falses;
    }
    aiButtons.forEach(function (b) {
      b.addEventListener("click", function () { setMode(b.dataset.mode); });
    });
    setMode("ai");
  }

  /* ---------- license keys: show / copy ---------- */
  function maskKey(key) { return key.slice(0, 5) + "••••-••••-••••-" + key.slice(-4); }

  document.addEventListener("click", function (e) {
    var revealBtn = e.target.closest("[data-reveal]");
    if (revealBtn) {
      var code = revealBtn.parentElement.querySelector(".key");
      var masked = code.dataset.masked === "true";
      code.textContent = masked ? code.dataset.key : maskKey(code.dataset.key);
      code.dataset.masked = String(!masked);
      revealBtn.setAttribute("aria-label", masked ? tr("hide-key", "Hide key") : tr("show-key", "Show key"));
      return;
    }
    var copyBtn = e.target.closest("[data-copy]");
    if (copyBtn) {
      var keyEl = copyBtn.parentElement.querySelector(".key");
      var value = keyEl.dataset.key || keyEl.textContent.trim();
      copyText(value).then(function () { toast(tr("copied", "Key copied")); }, function () { toast(tr("copy-failed", "Copy failed, select the key manually")); });
      return;
    }
    var useBtn = e.target.closest("[data-use-id]");
    if (useBtn) {
      var input = $("#grant-discord-id");
      if (input) {
        input.value = useBtn.dataset.useId;
        input.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
        input.focus({ preventScroll: true });
      }
    }
  });

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(area);
      ok ? resolve() : reject();
    });
  }

  /* ---------- admin: table filters and confirmations ---------- */
  $$("[data-filter]").forEach(function (input) {
    var table = $(input.dataset.filter);
    if (!table) return;
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      $$("tbody tr", table).forEach(function (row) {
        row.hidden = q !== "" && row.textContent.toLowerCase().indexOf(q) === -1;
      });
    });
  });

  $$("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });
  /* ---------- language menu: close on outside click or Escape ---------- */
  var langMenu = $(".lang-menu");
  if (langMenu) {
    document.addEventListener("click", function (e) {
      if (langMenu.open && !langMenu.contains(e.target)) langMenu.open = false;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && langMenu.open) {
        langMenu.open = false;
        langMenu.querySelector("summary").focus();
      }
    });
  }

  /* ---------- cookie notice: remember the choice without reloading ---------- */
  var cookieOk = $("[data-cookie-ok]");
  if (cookieOk) {
    cookieOk.addEventListener("click", function (e) {
      e.preventDefault();
      var banner = $("#cookie-banner");
      fetch(cookieOk.getAttribute("href"), { credentials: "same-origin", redirect: "manual" })
        .catch(function () {})
        .then(function () { if (banner) banner.remove(); });
    });
  }
  // wiki: show the checks included in a plan
  var planButtons = document.querySelectorAll("[data-plan-filter]");
  if (planButtons.length) {
    var order = ["free", "pro"];
    planButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var plan = btn.getAttribute("data-plan-filter");
        var max = order.indexOf(plan);
        planButtons.forEach(function (b) {
          var on = b === btn;
          b.setAttribute("aria-pressed", on ? "true" : "false");
          b.classList.toggle("btn-primary", on);
          b.classList.toggle("btn-outline", !on);
        });
        document.querySelectorAll(".wiki-checks tr[data-plan]").forEach(function (row) {
          var hidden = plan !== "all" && order.indexOf(row.getAttribute("data-plan")) > max;
          row.classList.toggle("is-hidden", hidden);
        });
      });
    });
  }
  // docs: copy buttons on code blocks
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy-code]");
    if (!btn) return;
    var text = btn.parentElement.querySelector("code").textContent;
    copyText(text).then(function () { toast(tr("code-copied", "Copied")); }, function () { toast(tr("code-copy-failed", "Copy failed, select the text manually")); });
  });

  // docs: search box filters sections and table rows
  var docSearch = document.getElementById("doc-search");
  if (docSearch) {
    var sections = Array.prototype.slice.call(document.querySelectorAll(".doc-section"));
    var empty = document.getElementById("doc-noresults");
    docSearch.addEventListener("input", function () {
      var q = docSearch.value.trim().toLowerCase();
      var shown = 0;
      sections.forEach(function (sec) {
        var rows = sec.querySelectorAll("tbody tr");
        var rowHits = 0;
        rows.forEach(function (row) {
          var hit = !q || row.textContent.toLowerCase().indexOf(q) !== -1;
          row.classList.toggle("is-search-hidden", !hit);
          if (hit) rowHits++;
        });
        var match = !q || sec.textContent.toLowerCase().indexOf(q) !== -1;
        if (match && q && rowHits === 0) rows.forEach(function (row) { row.classList.remove("is-search-hidden"); });
        sec.hidden = !match;
        if (match) shown++;
      });
      if (empty) empty.hidden = shown > 0;
    });
  }
  /* ---------- panel: local times, chart tooltips, live feed ---------- */
  var pageLang = document.documentElement.lang || "en";
  var rtf = window.Intl && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(pageLang, { numeric: "auto" }) : null;

  function formatAgo(ms) {
    var diff = (ms - Date.now()) / 1000;
    var abs = Math.abs(diff);
    if (!rtf) return new Date(ms).toLocaleString(pageLang);
    if (abs < 45) return rtf.format(0, "second");
    if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
    return rtf.format(Math.round(diff / 86400), "day");
  }

  function renderTimes(root) {
    $$(".js-time", root).forEach(function (el) {
      var ms = Number(el.getAttribute("data-ms"));
      if (!ms) return;
      var local = new Date(ms).toLocaleString(pageLang, { dateStyle: "medium", timeStyle: "short" });
      if (el.getAttribute("data-mode") === "ago") {
        el.textContent = formatAgo(ms);
        el.title = local;
      } else {
        el.textContent = local;
      }
    });
  }
  renderTimes(document);
  if (document.querySelector(".js-time")) setInterval(function () { renderTimes(document); }, 30000);

  $$("select[data-autosubmit]").forEach(function (sel) {
    sel.addEventListener("change", function () { sel.form.submit(); });
  });

  // chart tooltips: the SVG <title> text, shown next to the pointer (and on keyboard focus)
  var tip = null;
  $$(".chart-svg g").forEach(function (g) {
    var t = g.querySelector("title");
    if (!t) return;
    g.setAttribute("aria-label", t.textContent);
    g.dataset.tip = t.textContent;
    t.remove();
    function show(x, y) {
      if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; document.body.appendChild(tip); }
      tip.textContent = g.dataset.tip;
      tip.hidden = false;
      var w = tip.offsetWidth;
      tip.style.left = Math.min(window.innerWidth - w - 8, x + 14) + "px";
      tip.style.top = (y + 14) + "px";
    }
    function hide() { if (tip) tip.hidden = true; }
    g.addEventListener("mousemove", function (e) { show(e.clientX, e.clientY); });
    g.addEventListener("mouseleave", hide);
    g.addEventListener("focus", function () { var r = g.getBoundingClientRect(); show(r.left + r.width / 2, r.top); });
    g.addEventListener("blur", hide);
  });

  // live feed: poll this website (never the license server) for new events; "Load more" pages back
  var feed = document.getElementById("feed");
  if (feed && feed.dataset.feedUrl) {
    var feedUrl = feed.dataset.feedUrl;
    var moreBtn = document.getElementById("feed-more");
    var statusEl = document.getElementById("feed-status");
    var emptyEl = document.getElementById("feed-empty");
    var joiner = feedUrl.indexOf("?") === -1 ? "?" : "&";

    function newest() {
      var max = 0;
      $$(".feed-row", feed).forEach(function (row) { max = Math.max(max, Number(row.dataset.time) || 0); });
      return max;
    }
    function rowsFrom(html) {
      var tpl = document.createElement("template");
      tpl.innerHTML = html;
      return Array.prototype.slice.call(tpl.content.querySelectorAll(".feed-row"));
    }
    function load(url) {
      return fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    }
    function setLive(ok) { if (statusEl) statusEl.classList.toggle("is-paused", !ok); }

    function poll() {
      if (document.hidden) return;
      var since = newest();
      load(feedUrl).then(function (data) {
        setLive(true);
        // server cards too: a server that just started appears without reloading the page
        var box = document.getElementById("servers-box");
        if (box && typeof data.servers === "string") {
          var tpl = document.createElement("template");
          tpl.innerHTML = data.servers;
          box.replaceChildren(tpl.content);
          renderTimes(box);
        }
        var fresh = rowsFrom(data.html).filter(function (row) { return Number(row.dataset.time) > since; });
        for (var i = fresh.length - 1; i >= 0; i--) {
          fresh[i].classList.add("is-new");
          feed.insertBefore(fresh[i], feed.firstChild);
        }
        if (fresh.length) {
          renderTimes(feed);
          if (emptyEl) emptyEl.hidden = true;
        }
      }).catch(function () { setLive(false); });
    }
    setInterval(poll, 12000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });

    if (moreBtn) {
      moreBtn.addEventListener("click", function () {
        var next = feed.dataset.next;
        if (!next || next === "0") return;
        moreBtn.disabled = true;
        load(feedUrl + joiner + "before=" + encodeURIComponent(next)).then(function (data) {
          rowsFrom(data.html).forEach(function (row) { feed.appendChild(row); });
          renderTimes(feed);
          feed.dataset.next = data.next || 0;
          moreBtn.hidden = !data.next;
        }).catch(function () { setLive(false); }).then(function () { moreBtn.disabled = false; });
      });
    }
  }
  // setup page: show a question only when the answer it depends on has the right value
  var dependents = $$("[data-show-if]");
  if (dependents.length) {
    var form = dependents[0].form || document.querySelector(".setup-form");
    function currentValue(name) {
      var checked = form.querySelector('input[name="' + name + '"]:checked');
      if (checked) return checked.value;
      var field = form.querySelector('[name="' + name + '"]');
      return field ? field.value : "";
    }
    function refresh() {
      dependents.forEach(function (box) {
        var parts = box.getAttribute("data-show-if").split("=");
        var show = currentValue(parts[0]) === parts.slice(1).join("=");
        box.hidden = !show;
      });
    }
    form.addEventListener("change", refresh);
    refresh();
  }
  // customer area: "Mythras x.y is out" banner, hidden for good once closed (remembered per version)
  var news = document.querySelector("[data-news-banner]");
  if (news) {
    var seen = null;
    try { seen = localStorage.getItem("mythras-news-closed"); } catch (e) { seen = null; }
    if (seen !== news.dataset.version) news.hidden = false;
    news.querySelector("[data-news-close]").addEventListener("click", function () {
      news.hidden = true;
      try { localStorage.setItem("mythras-news-closed", news.dataset.version); } catch (e) { /* private mode */ }
    });
  }
  // shop: back from PayPal but not confirmed yet. Ask our own server again every 3 s for 60 s (it asks the
  // license server, which reads PayPal); reload when paid so the server renders the key
  var wait = document.querySelector("[data-shop-poll]");
  if (wait) {
    var tries = 0;
    var poll = function () {
      tries += 1;
      var body = new URLSearchParams({ id: wait.dataset.id, csrf_token: wait.dataset.csrf });
      fetch(wait.dataset.url, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.paid) { window.location.reload(); return; }
          if (tries < 20) { setTimeout(poll, 3000); return; }
          wait.querySelector("[data-wait-text]").hidden = true;
          wait.querySelector("[data-slow-text]").hidden = false;
        })
        .catch(function () { if (tries < 20) setTimeout(poll, 3000); });
    };
    setTimeout(poll, 3000);
  }
})();
