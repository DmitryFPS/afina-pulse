const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  status: null,
  view: "dash",
  tgMethod: "qr",
  fbMethod: "password",
};

function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.style.borderColor = isErr ? "#5a2a32" : "#273140";
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 4200);
}

async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs || 20000);
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
      signal: ctrl.signal,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try {
      data = await res.json();
    } catch {
      data = { error: `HTTP ${res.status}` };
    }
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || data.detail || `Ошибка ${res.status}`);
    }
    return data;
  } catch (err) {
    if (err.name === "AbortError") throw new Error("Сервер не ответил за 20 секунд");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function idleLabel(btn) {
  if (!btn) return "";
  const current = (btn.textContent || "").trim();
  if (btn.dataset.label && btn.dataset.label !== "Проверяю…") return btn.dataset.label;
  if (current && current !== "Проверяю…") {
    btn.dataset.label = current;
    return current;
  }
  return btn.dataset.label || "Готово";
}

function setBusy(btn, busy) {
  if (!btn) return;
  const label = idleLabel(btn);
  btn.dataset.label = label;
  btn.disabled = !!busy;
  btn.textContent = busy ? "Проверяю…" : label;
}

function clearBusyAll() {
  $$("button").forEach((btn) => {
    if ((btn.textContent || "").includes("Проверяю")) setBusy(btn, false);
    else if (btn.dataset.label) {
      btn.disabled = false;
      btn.textContent = btn.dataset.label;
    }
  });
}

function humanConn(c) {
  if (!c || c.status === "disconnected") return "не подключено";
  if (c.status === "pending_code") return "ждёт код из Telegram";
  if (c.status === "pending_qr") return "ждёт сканирование QR";
  if (c.status === "pending_2fa") return "ждёт 2FA";
  if (c.status === "error") return c.error || "ошибка";
  return c.label || c.method || "подключено";
}

function showApp(connected) {
  $("#gate").classList.toggle("hidden", connected);
  $("#shell").classList.toggle("hidden", !connected);
}

function renderStatusPills() {
  const s = state.status;
  if (!s) return;
  $("#tg-dot")?.classList.toggle("on", s.telegram?.status === "connected");
  $("#fb-dot")?.classList.toggle("on", s.facebook?.status === "connected");
  const tgLabel = $("#tg-status-label");
  const fbLabel = $("#fb-status-label");
  if (tgLabel) tgLabel.textContent = humanConn(s.telegram);
  if (fbLabel) fbLabel.textContent = humanConn(s.facebook);
}

function paintQr(imgId, emptyId, url) {
  const img = $(imgId);
  const empty = $(emptyId);
  if (!url) return;
  img.src = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(url)}`;
  img.classList.add("show");
  if (empty) empty.style.display = "none";
}

function renderCatalog() {
  const s = state.status;
  $("#gate-tg-state").textContent = humanConn(s.telegram);
  $("#gate-fb-state").textContent = humanConn(s.facebook);
  $("#enter-btn").disabled = !s.connected_any;
  if (s.telegram?.status === "pending_code") $("#tg-code-wrap").classList.remove("hidden");
  if (s.telegram?.status === "pending_2fa") {
    $("#tg-2fa-wrap").classList.remove("hidden");
    $("#tg-qr-2fa").classList.remove("hidden");
  }
  if (s.facebook?.status === "pending_2fa") {
    $("#fb-2fa-wrap").classList.remove("hidden");
    $("#fb-qr-2fa").classList.remove("hidden");
  }
}

async function refreshStatus() {
  state.status = await api("/api/status");
  renderStatusPills();
  const connected = !!state.status.connected_any;
  if (connected) {
    showApp(true);
    await refreshDashboard();
  } else {
    showApp(false);
    renderCatalog();
  }
}

function switchMethod(group, id) {
  $$(`.tabs[data-group="${group}"] .tab`).forEach((t) => t.classList.toggle("active", t.dataset.method === id));
  $$(`.method[data-group="${group}"]`).forEach((m) => m.classList.toggle("active", m.dataset.method === id));
  if (group === "tg") state.tgMethod = id;
  if (group === "fb") state.fbMethod = id;
}

async function connectTelegram(method, payload, btn) {
  setBusy(btn, true);
  $("#tg-err").textContent = "";
  try {
    const data = await api(`/api/telegram/connect/${method}`, { method: "POST", body: payload });
    if (data.next === "code") {
      $("#tg-code-wrap").classList.remove("hidden");
      toast(data.detail || "Введите код из Telegram");
    } else if (data.next === "2fa") {
      $("#tg-2fa-wrap").classList.remove("hidden");
      $("#tg-qr-2fa").classList.remove("hidden");
      toast(data.detail || "Введите облачный пароль 2FA");
    } else {
      toast(data.detail || "Telegram подключён");
    }
    await refreshStatus();
  } catch (e) {
    $("#tg-err").textContent = e.message;
    toast(e.message, true);
  } finally {
    setBusy(btn, false);
  }
}

async function connectFacebook(method, payload, btn) {
  setBusy(btn, true);
  $("#fb-err").textContent = "";
  try {
    if (method === "oauth") {
      const data = await api("/api/facebook/oauth/start", { method: "POST", body: payload });
      window.location.href = data.url;
      return;
    }
    const data = await api(`/api/facebook/connect/${method}`, { method: "POST", body: payload });
    if (data.next === "2fa") {
      $("#fb-2fa-wrap").classList.remove("hidden");
      toast(data.detail || "Введите код 2FA");
    } else {
      toast(data.detail || data.probe?.limitation || "Facebook подключён");
    }
    await refreshStatus();
  } catch (e) {
    $("#fb-err").textContent = e.message;
    toast(e.message, true);
  } finally {
    setBusy(btn, false);
  }
}

async function disconnect(platform) {
  await api(`/api/${platform}/connect`, { method: "DELETE" });
  if (platform === "telegram") {
    $("#tg-code-wrap").classList.add("hidden");
    $("#tg-2fa-wrap").classList.add("hidden");
    $("#tg-qr-2fa").classList.add("hidden");
    resetQrUi("telegram", "QR сброшен. Можно показать новый.");
    $("#tg-err").textContent = "";
  } else {
    $("#fb-2fa-wrap").classList.add("hidden");
    $("#fb-qr-2fa").classList.add("hidden");
    resetQrUi("facebook", "QR сброшен. Можно показать новый.");
    $("#fb-err").textContent = "";
  }
  clearBusyAll();
  await refreshStatus();
}

function resetQrUi(platform, message) {
  const isTg = platform === "telegram";
  const startBtn = $(isTg ? "#tg-qr-start" : "#fb-qr-start");
  const scannedBtn = $(isTg ? "#tg-qr-scanned" : "#fb-qr-scanned");
  const statusEl = $(isTg ? "#tg-qr-status" : "#fb-qr-status");
  setBusy(startBtn, false);
  setBusy(scannedBtn, false);
  if (scannedBtn) scannedBtn.disabled = true;
  if (statusEl && message) statusEl.textContent = message;
  $(isTg ? "#tg-qr-2fa" : "#fb-qr-2fa")?.classList.add("hidden");
}

async function startQr(platform) {
  const data = await api(`/api/${platform}/connect/qr/start`, { method: "POST", body: {} });
  const url = data.qr?.url;
  const note = data.detail || "Наведите камеру на QR.";
  if (platform === "telegram") {
    paintQr("#tg-qr-img", "#tg-qr-empty", url);
    $("#tg-qr-status").textContent = note;
    $("#tg-qr-scanned").disabled = false;
    $("#tg-qr-2fa").classList.add("hidden");
  } else {
    paintQr("#fb-qr-img", "#fb-qr-empty", url);
    $("#fb-qr-status").textContent = note;
    $("#fb-qr-scanned").disabled = false;
    $("#fb-qr-2fa").classList.add("hidden");
  }
  toast(note);
  await refreshStatus();
  showApp(false);
  renderCatalog();
}

async function confirmQr(platform, extra = {}) {
  const data = await api(`/api/${platform}/connect/qr/confirm`, { method: "POST", body: extra });
  if (data.next === "2fa") {
    if (platform === "telegram") $("#tg-qr-2fa").classList.remove("hidden");
    else $("#fb-qr-2fa").classList.remove("hidden");
    toast(data.detail);
  } else {
    toast("Вход подтверждён");
  }
  await refreshStatus();
  if (data.next !== "done") {
    showApp(false);
    renderCatalog();
  }
}

async function refreshDashboard() {
  const [matches, rules, sources, archives, windowInfo] = await Promise.all([
    api("/api/matches?limit=30"),
    api("/api/rules"),
    api("/api/sources"),
    api("/api/archives"),
    api("/api/window"),
  ]);
  $("#stat-items").textContent = String(windowInfo.hot_items ?? 0);
  $("#stat-matches").textContent = String(windowInfo.hot_matches ?? 0);
  $("#stat-rules").textContent = String((rules.items || []).length);
  $("#stat-archives").textContent = String((archives.items || []).length);
  $("#stat-window").textContent = `${windowInfo.window?.lookback_days || 7} дней`;
  renderFeed(matches.items || [], "#feed-list");
  renderFeed(matches.items || [], "#feed-full");
  renderRules(rules.items || []);
  renderSources(sources.items || []);
  renderArchives(archives.items || []);
}

function renderFeed(items, sel) {
  const box = $(sel);
  if (!items.length) {
    box.innerHTML = `<div class="empty">Совпадений пока нет. Подключите источники и добавьте правило.</div>`;
    return;
  }
  box.innerHTML = items.map((it) => {
    const plat = it.platform === "facebook" ? "fb" : "tg";
    return `<article class="card feed-item">
      <div class="meta">
        <span class="badge ${plat}">${it.platform || ""}</span>
        <span>${escapeHtml(it.source_id || "")}</span>
        <span>${escapeHtml((it.published_at || it.created_at || "").replace("T", " ").slice(0, 16))}</span>
        <span class="badge">${escapeHtml(it.rule_ids || "")}</span>
      </div>
      <div>${escapeHtml((it.search_blob || it.permalink || it.item_id || "").slice(0, 280))}</div>
    </article>`;
  }).join("");
}

function renderRules(items) {
  const box = $("#rules-list");
  if (!items.length) {
    box.innerHTML = `<div class="empty">Правил нет</div>`;
    return;
  }
  box.innerHTML = items.map((r) => `<div class="card" style="margin-bottom:10px">
    <div style="display:flex;justify-content:space-between;gap:12px">
      <div>
        <b>${escapeHtml(r.id)}</b>
        <div class="s">keywords: ${(r.keywords || []).join(", ") || "—"}</div>
        <div class="s">phrases: ${(r.phrases || []).join(", ") || "—"}</div>
      </div>
      <div>
        <span class="badge ${r.enabled ? "on" : "off"}">${r.enabled ? "ON" : "OFF"}</span>
        <button class="btn ghost" data-del-rule="${escapeHtml(r.id)}">удалить</button>
      </div>
    </div>
  </div>`).join("");
}

function renderSources(items) {
  const box = $("#sources-list");
  if (!items.length) {
    box.innerHTML = `<div class="empty">Источники появятся после подключения Graph / Relay или добавьте вручную.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Платформа</th><th>Источник</th><th>Тип</th><th></th></tr></thead><tbody>${
    items.map((s) => `<tr>
      <td><span class="badge ${s.platform === "facebook" ? "fb" : "tg"}">${s.platform}</span></td>
      <td>${escapeHtml(s.title || s.source_id)}</td>
      <td>${escapeHtml(s.kind || "")}</td>
      <td><button class="btn ghost" data-del-source="${escapeHtml(s.id)}">удалить</button></td>
    </tr>`).join("")
  }</tbody></table>`;
}

function renderArchives(items) {
  const box = $("#archives-list");
  if (!items.length) {
    box.innerHTML = `<div class="empty">Архивов ещё нет. Окно 7 дней можно закрыть кнопкой ниже.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Период</th><th>Событий</th><th>Совпадений</th><th>Файл</th></tr></thead><tbody>${
    items.map((a) => `<tr>
      <td class="mono">${escapeHtml((a.since || "").slice(0, 10))} — ${escapeHtml((a.until || "").slice(0, 10))}</td>
      <td>${a.items ?? "—"}</td>
      <td>${a.matches ?? "—"}</td>
      <td>${escapeHtml(a.path || "")}</td>
    </tr>`).join("")
  }</tbody></table>`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function showView(id) {
  state.view = id;
  $$(".page").forEach((p) => p.classList.toggle("hidden", p.id !== `page-${id}`));
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === id));
}

function bind() {
  $$(".tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => switchMethod(tab.parentElement.dataset.group, tab.dataset.method));
  });
  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  $("#enter-btn").addEventListener("click", () => {
    if (state.status?.connected_any) showApp(true);
  });
  $("#back-gate").addEventListener("click", () => showApp(false));

  $("#tg-qr-start").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try { await startQr("telegram"); }
    catch (err) { $("#tg-err").textContent = err.message; toast(err.message, true); }
    finally { setBusy(btn, false); }
  });
  $("#tg-qr-scanned").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try { await confirmQr("telegram"); }
    catch (err) {
      $("#tg-err").textContent = err.message;
      toast(err.message, true);
      resetQrUi("telegram", err.message);
    }
    finally { setBusy(btn, false); }
  });
  $("#tg-qr-2fa-go").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try { await confirmQr("telegram", { password: $("#tg-qr-password").value }); }
    catch (err) { toast(err.message, true); }
    finally { setBusy(btn, false); }
  });
  $("#tg-qr-skip").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try {
      await api("/api/telegram/connect/qr/skip-2fa", { method: "POST", body: {} });
      toast("Telegram: QR без живой MTProto-сессии не подключает аккаунт");
      await refreshStatus();
    } catch (err) { toast(err.message, true); }
    finally { setBusy(btn, false); }
  });

  $("#tg-phone-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const apiId = $("#tg-api-id").value.trim();
    connectTelegram("phone", {
      phone: $("#tg-phone").value.trim(),
      api_id: apiId ? Number(apiId) : null,
      api_hash: $("#tg-api-hash").value.trim() || null,
    }, e.submitter);
  });
  $("#tg-code-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectTelegram("code", { code: $("#tg-code").value.trim() }, e.submitter);
  });
  $("#tg-2fa-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter || $("#tg-2fa-form button[type=submit]");
    setBusy(btn, true);
    try {
      await api("/api/telegram/connect/2fa", { method: "POST", body: { password: $("#tg-2fa").value } });
      toast("Telegram: 2FA принят");
      await refreshStatus();
    } catch (err) {
      $("#tg-err").textContent = err.message;
      toast(err.message, true);
    } finally {
      setBusy(btn, false);
    }
  });

  $("#fb-pass-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectFacebook("password", {
      login: $("#fb-login").value.trim(),
      password: $("#fb-password").value,
    }, e.submitter);
  });
  $("#fb-2fa-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter || $("#fb-2fa-form button[type=submit]");
    setBusy(btn, true);
    try {
      await api("/api/facebook/connect/2fa", { method: "POST", body: { code: $("#fb-2fa").value.trim() } });
      toast("Facebook: 2FA принят");
      await refreshStatus();
    } catch (err) {
      $("#fb-err").textContent = err.message;
      toast(err.message, true);
    } finally {
      setBusy(btn, false);
    }
  });

  $("#fb-qr-start").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try { await startQr("facebook"); }
    catch (err) { $("#fb-err").textContent = err.message; toast(err.message, true); }
    finally { setBusy(btn, false); }
  });
  $("#fb-qr-scanned").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try { await confirmQr("facebook"); }
    catch (err) {
      $("#fb-err").textContent = err.message;
      toast(err.message, true);
      resetQrUi("facebook", err.message);
    }
    finally { setBusy(btn, false); }
  });
  $("#fb-qr-2fa-go").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try { await confirmQr("facebook", { code: $("#fb-qr-code").value.trim() }); }
    catch (err) { toast(err.message, true); }
    finally { setBusy(btn, false); }
  });
  $("#fb-qr-skip").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true);
    try {
      await api("/api/facebook/connect/qr/skip-2fa", { method: "POST", body: {} });
      toast("Facebook: QR без живого OAuth не подключает аккаунт");
      await refreshStatus();
    } catch (err) { toast(err.message, true); }
    finally { setBusy(btn, false); }
  });

  $("#tg-relay-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectTelegram("relay", { url: $("#tg-relay-url").value.trim() }, e.submitter);
  });
  $("#tg-session-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectTelegram("session", { path: $("#tg-session-path").value.trim() }, e.submitter);
  });
  $("#tg-bot-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectTelegram("bot", { token: $("#tg-bot-token").value.trim() }, e.submitter);
  });
  $("#fb-token-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectFacebook("token", {
      token: $("#fb-token").value.trim(),
      app_id: $("#fb-app-id").value.trim() || null,
      app_secret: $("#fb-app-secret").value.trim() || null,
    }, e.submitter);
  });
  $("#fb-oauth-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectFacebook("oauth", {
      app_id: $("#fb-oauth-id").value.trim(),
      app_secret: $("#fb-oauth-secret").value.trim(),
    }, e.submitter);
  });
  $("#fb-import-form").addEventListener("submit", (e) => {
    e.preventDefault();
    connectFacebook("import", { path: $("#fb-import-path").value.trim() }, e.submitter);
  });

  $("#tg-disconnect")?.addEventListener("click", () => disconnect("telegram"));
  $("#fb-disconnect")?.addEventListener("click", () => disconnect("facebook"));

  $("#rule-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/rules", {
        method: "POST",
        body: {
          id: $("#rule-id").value.trim(),
          keywords: splitList($("#rule-kw").value),
          phrases: splitList($("#rule-ph").value),
          semantic_threshold: Number($("#rule-th").value || 0.72),
          always_llm: $("#rule-llm").checked,
          enabled: true,
        },
      });
      toast("Правило сохранено");
      await refreshDashboard();
      e.target.reset();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#source-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/sources", {
        method: "POST",
        body: {
          platform: $("#src-platform").value,
          source_id: $("#src-id").value.trim(),
          title: $("#src-title").value.trim() || null,
          kind: $("#src-kind").value.trim() || "custom",
        },
      });
      toast("Источник добавлен");
      await refreshDashboard();
      e.target.reset();
    } catch (err) {
      toast(err.message, true);
    }
  });

  document.addEventListener("click", async (e) => {
    const delR = e.target.closest("[data-del-rule]");
    const delS = e.target.closest("[data-del-source]");
    try {
      if (delR) {
        await api(`/api/rules/${encodeURIComponent(delR.dataset.delRule)}`, { method: "DELETE" });
        await refreshDashboard();
      }
      if (delS) {
        await api(`/api/sources/${encodeURIComponent(delS.dataset.delSource)}`, { method: "DELETE" });
        await refreshDashboard();
      }
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#archive-close")?.addEventListener("click", async (e) => {
    setBusy(e.currentTarget, true);
    try {
      await api("/api/archives/close", { method: "POST" });
      toast("Окно упаковано");
      await refreshDashboard();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(e.currentTarget, false);
    }
  });
}

function splitList(s) {
  return s.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
}

window.addEventListener("DOMContentLoaded", async () => {
  clearBusyAll();
  bind();
  const params = new URLSearchParams(location.search);
  if (params.get("fb") === "ok") toast("Facebook подключён через OAuth");
  if (params.get("fb") === "denied") toast("Facebook: доступ отклонён", true);
  if (params.get("fb") === "error") toast("Facebook: ошибка обмена кода", true);
  try {
    await refreshStatus();
  } catch (e) {
    toast("API недоступен: " + e.message, true);
    showApp(false);
  }
});
