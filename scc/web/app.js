/* Server Control Web - клиентское ядро (без зависимостей). */
(function () {
  'use strict';

  var SCC = window.SCC = {
    state: { boot: null, user: null, csrf: null, node: 'local', nodes: null, nodeInfo: null, nodeModules: null },
    pages: {}, settingsTabs: [], menuPlus: [], mounts: { 'header-right': [], 'page-actions': [] }, extraNav: [],
    _on: {}, _cleanups: []
  };

  /* ====================================================== утилиты */
  function h(tag, attrs) {
    var el = document.createElement(tag), deferred = null;
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') el.className = v;
      else if (k === 'style') { if (typeof v === 'object') Object.assign(el.style, v); else el.style.cssText = v; }
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'value') deferred = v;
      else if (k === 'for') el.htmlFor = v;
      else if (k.indexOf('-') > 0 || k === 'role') el.setAttribute(k, v);
      else if (k in el) { try { el[k] = v; } catch (e) { el.setAttribute(k, v); } }
      else el.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    if (deferred !== null) el.value = deferred;
    return el;
  }
  function append(el, c) {
    if (c == null || c === false || c === true) return;
    if (Array.isArray(c)) c.forEach(function (x) { append(el, x); });
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
  function icon(name, size, cls) { return window.sccIcon(name, size, cls); }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function $(sel, root) { return (root || document).querySelector(sel); }

  var fmt = {
    bytes: function (n) {
      if (n == null || isNaN(n)) return '-';
      var u = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ', 'ПБ'], i = 0; n = Number(n);
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return (i === 0 ? n : n.toFixed(n >= 100 ? 0 : 1)) + ' ' + u[i];
    },
    ago: function (ts) {
      if (!ts) return '-';
      var s = Math.max(0, Date.now() / 1000 - ts);
      if (s < 45) return 'только что';
      if (s < 3600) return Math.round(s / 60) + ' мин. назад';
      if (s < 86400) return Math.round(s / 3600) + ' ч. назад';
      if (s < 86400 * 30) return Math.round(s / 86400) + ' дн. назад';
      return new Date(ts * 1000).toLocaleDateString('ru-RU');
    },
    date: function (ts) { return ts ? new Date(ts * 1000).toLocaleString('ru-RU') : '-'; },
    duration: function (s) {
      s = Math.floor(s || 0); var d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
      return (d ? d + ' д ' : '') + (d || hh ? hh + ' ч ' : '') + m + ' мин';
    },
    pct: function (v) { return (v == null ? '-' : v.toFixed(1)) + '%'; }
  };

  function ApiError(message, status, data) { this.message = message; this.status = status; this.data = data; }
  ApiError.prototype = Object.create(Error.prototype);

  /* ====================================================== события */
  SCC.on = function (ev, fn) { (SCC._on[ev] = SCC._on[ev] || []).push(fn); };
  SCC.emit = function (ev, a) { (SCC._on[ev] || []).forEach(function (f) { try { f(a); } catch (e) { console.error(e); } }); };

  /* ====================================================== сеть */
  SCC.url = function (path, node) {
    node = node || SCC.state.node;
    return node === 'local' ? path : '/node/' + encodeURIComponent(node) + path;
  };
  SCC.api = function (url, opts) {
    opts = opts || {};
    var o = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
    if (SCC.state.csrf) o.headers['X-SCC-CSRF'] = SCC.state.csrf;
    if (opts.json !== undefined) {
      o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.json);
      if (o.method === 'GET') o.method = 'POST';
    } else if (opts.body !== undefined) {
      o.body = opts.body; if (opts.type) o.headers['Content-Type'] = opts.type;
      if (o.method === 'GET') o.method = 'POST';
    }
    return fetch(url, o).then(function (r) {
      if (opts.raw) return r;
      var ct = r.headers.get('content-type') || '';
      var p = ct.indexOf('json') >= 0 ? r.json().catch(function () { return null; }) : r.text();
      return p.then(function (data) {
        if (r.status === 401 && !opts.quiet && SCC.state.user) { SCC.state.user = null; start(); }
        if (!r.ok) throw new ApiError((data && data.error) || r.statusText || ('HTTP ' + r.status), r.status, data);
        return data;
      });
    });
  };
  /* Помощник для API модуля: SCC.mod('files').get('list', {path: '/'}) - автоматически идёт на выбранную ноду. */
  SCC.mod = function (id, local) {
    function build(path, q) {
      var u = '/api/m/' + id + '/' + String(path).replace(/^\//, '');
      if (q) {
        var qs = Object.keys(q).filter(function (k) { return q[k] != null; })
          .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(q[k]); }).join('&');
        if (qs) u += '?' + qs;
      }
      return local ? u : SCC.url(u);
    }
    return {
      url: build,
      get: function (p, q) { return SCC.api(build(p, q)); },
      post: function (p, json, q) { return SCC.api(build(p, q), { method: 'POST', json: json === undefined ? {} : json }); },
      put: function (p, body, q, type) { return SCC.api(build(p, q), { method: 'PUT', body: body, type: type }); },
      del: function (p, q) { return SCC.api(build(p, q), { method: 'DELETE' }); }
    };
  };

  /* ====================================================== UI-примитивы */
  SCC.toast = function (msg, kind) {
    var t = h('div', { class: 'toast ' + (kind || 'info') }, icon(kind === 'error' ? 'x-circle' : kind === 'success' ? 'check-circle' : 'info'), h('span', null, msg));
    $('#toasts').appendChild(t);
    setTimeout(function () { t.remove(); }, kind === 'error' ? 7000 : 3500);
  };
  SCC.err = function (e) { SCC.toast((e && e.message) || String(e), 'error'); };

  SCC.dialog = function (o) {
    var bd = h('div', { class: 'backdrop' });
    var close = function (v) { document.removeEventListener('keydown', onKey); bd.remove(); if (o.onClose) o.onClose(v); };
    var onKey = function (e) { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    bd.addEventListener('mousedown', function (e) { if (e.target === bd) close(null); });
    var foot = o.actions && o.actions.length ? h('div', { class: 'Overlay-footer' }, o.actions.map(function (a) {
      var b = h('button', { class: 'btn ' + (a.kind ? 'btn-' + a.kind : ''), type: 'button', onclick: function () { a.onclick ? a.onclick(close, b) : close(a.value); } }, a.label);
      return b;
    })) : null;
    var box = h('div', { class: 'Overlay' + (o.size ? ' ' + o.size : ''), role: 'dialog', 'aria-modal': 'true' },
      h('div', { class: 'Overlay-header' }, h('h3', null, o.title), h('button', { class: 'IconBtn plain', type: 'button', 'aria-label': 'Закрыть', onclick: function () { close(null); } }, icon('x'))),
      h('div', { class: 'Overlay-body' }, o.body), foot);
    bd.appendChild(box); document.body.appendChild(bd);
    var f = $('input,textarea,select', box); if (f && !o.noFocus) setTimeout(function () { f.focus(); }, 30);
    return { close: close, el: box };
  };
  SCC.confirm = function (title, text, o) {
    o = o || {};
    return new Promise(function (res) {
      SCC.dialog({ title: title, body: h('div', null, text), onClose: function (v) { res(v === true); },
        actions: [{ label: 'Отмена', value: false }, { label: o.ok || 'Подтвердить', kind: o.danger ? 'danger' : 'primary', value: true }] });
    });
  };
  SCC.prompt = function (title, label, o) {
    o = o || {};
    return new Promise(function (res) {
      var inp = h('input', { class: 'form-control' + (o.mono ? ' mono' : ''), type: o.type || 'text', value: o.value || '', placeholder: o.placeholder || '' });
      var d = SCC.dialog({ title: title, body: h('div', { class: 'form-group' }, h('label', null, label), inp),
        onClose: function (v) { res(v === true ? inp.value : null); },
        actions: [{ label: 'Отмена', value: false }, { label: o.ok || 'ОК', kind: 'primary', value: true }] });
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') d.close(true); });
    });
  };
  SCC.menu = function (btn, getItems, o) {
    o = o || {};
    var wrap = h('div', { class: 'dropdown' }, btn), open = null;
    function close() { if (open) { open.remove(); open = null; document.removeEventListener('mousedown', outside, true); } }
    function outside(e) { if (open && !wrap.contains(e.target)) close(); }
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (open) return close();
      var items = getItems(), m = h('div', { class: 'dropdown-menu' + (o.left ? ' left' : ''), role: 'menu' });
      items.forEach(function (it) {
        if (!it) return;
        if (it.divider) m.appendChild(h('div', { class: 'dropdown-divider' }));
        else if (it.header) m.appendChild(h('div', { class: 'dropdown-header' }, it.header));
        else m.appendChild(h('button', { class: 'dropdown-item' + (it.danger ? ' danger' : '') + (it.active ? ' active' : ''), type: 'button',
          onclick: function () { close(); it.onclick && it.onclick(); } }, it.icon ? icon(it.icon) : null, h('span', null, it.label), it.active ? h('span', { class: 'ml-auto' }, icon('check')) : null));
      });
      wrap.appendChild(m); open = m; document.addEventListener('mousedown', outside, true);
    });
    return wrap;
  };
  SCC.flash = function (kind, content, actions) {
    var ic = { warn: 'alert', error: 'x-circle', success: 'check-circle', info: 'info' }[kind] || 'info';
    return h('div', { class: 'flash flash-' + kind }, icon(ic), h('div', null, content), actions || null);
  };
  SCC.blank = function (ic, title, text, action) {
    return h('div', { class: 'blankslate' }, icon(ic, 32), h('h3', null, title), text ? h('p', null, text) : null, action || null);
  };
  SCC.copy = function (text) {
    var done = function () { SCC.toast('Скопировано', 'success'); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, function () { fallback(); });
    else fallback();
    function fallback() {
      var t = h('textarea', { style: 'position:fixed;opacity:0' }); t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand('copy'); done(); } catch (e) { SCC.toast('Не удалось скопировать', 'error'); } t.remove();
    }
  };
  SCC.busy = function (btn, fn) {
    btn.disabled = true;
    var p; try { p = Promise.resolve(fn()); } catch (e) { p = Promise.reject(e); }
    return p.catch(SCC.err).then(function (v) { btn.disabled = false; return v; });
  };
  SCC.h = h; SCC.icon = icon; SCC.fmt = fmt; SCC.clear = clear; SCC.$ = $; SCC.ApiError = ApiError;

  /* ====================================================== API расширений */
  SCC.registerPage = function (id, def) { SCC.pages[id] = def; };
  SCC.addSettingsTab = function (t) { SCC.settingsTabs.push(t); };
  SCC.addNav = function (item) { SCC.extraNav.push(item); };
  SCC.mount = function (slot, el) { (SCC.mounts[slot] = SCC.mounts[slot] || []).push(el); };
  SCC.link = function (page) {
    var args = Array.prototype.slice.call(arguments, 1).map(encodeURIComponent);
    var pre = SCC.state.node === 'local' ? '' : 'n/' + encodeURIComponent(SCC.state.node) + '/';
    return '#/' + pre + [page].concat(args).join('/');
  };
  SCC.go = function () { location.hash = SCC.link.apply(null, arguments); };
  SCC.theme = function (t) {
    if (t) { try { localStorage.setItem('scc-theme', t); } catch (e) { } document.documentElement.setAttribute('data-theme', t); }
    return document.documentElement.getAttribute('data-theme');
  };
  SCC.isAdmin = function () { return SCC.state.user && SCC.state.user.role === 'admin'; };

  /* ====================================================== запуск */
  var root = $('#app');
  function start() {
    SCC.api('/api/boot', { quiet: true }).then(function (b) {
      SCC.state.boot = b;
      if (!b.configured) return renderWizard(b);
      if (!b.authenticated) return renderLogin(b);
      SCC.state.user = b.user; SCC.state.csrf = b.csrf; SCC.state.nodes = b.nodes || null;
      loadAssets(b).then(function () { renderShell(); window.removeEventListener('hashchange', route); window.addEventListener('hashchange', route); route(); SCC.emit('ready'); });
    }).catch(function (e) {
      clear(root).appendChild(h('div', { class: 'Container Page' }, SCC.flash('error', 'Не удалось связаться с сервером: ' + e.message, h('button', { class: 'btn btn-sm', onclick: start }, 'Повторить'))));
    });
  }
  SCC.start = start;

  function loadScript(url) {
    return new Promise(function (res) {
      var s = document.createElement('script'); s.src = url; s.onload = res;
      s.onerror = function () { console.error('Не загрузился скрипт', url); res(); };
      document.head.appendChild(s);
    });
  }
  var loadedAssets = {};
  function loadAssets(b) {
    var seq = Promise.resolve();
    (b.modules || []).forEach(function (m) {
      (m.css || []).forEach(function (c) { if (!loadedAssets[c]) { loadedAssets[c] = 1; document.head.appendChild(h('link', { rel: 'stylesheet', href: c })); } });
    });
    (b.assets || []).forEach(function (a) {
      if (loadedAssets[a.url]) return; loadedAssets[a.url] = 1;
      if (a.kind === 'style') document.head.appendChild(h('link', { rel: 'stylesheet', href: a.url }));
      else seq = seq.then(function () { return loadScript(a.url); });
    });
    (b.modules || []).forEach(function (m) {
      if (m.ui && !loadedAssets[m.ui]) { loadedAssets[m.ui] = 1; seq = seq.then(function () { return loadScript(m.ui); }); }
    });
    return seq;
  }

  /* ====================================================== вход */
  function authFrame(title, body, wide) {
    clear(root).appendChild(h('div', { class: 'AuthWrap' },
      h('div', { class: 'logo' }, h('img', { src: '/favicon.svg', alt: '' })),
      h('h1', null, title),
      h('div', { class: 'AuthCard' + (wide ? ' wide' : '') }, body),
      h('p', { class: 'muted mt-3', style: 'font-size:12px' }, 'Server Control Web ' + (SCC.state.boot ? SCC.state.boot.version : ''))));
  }
  function renderLogin(b) {
    var u = h('input', { class: 'form-control', name: 'username', autocomplete: 'username', autofocus: true, required: true });
    var p = h('input', { class: 'form-control', name: 'password', type: 'password', autocomplete: 'current-password', required: true });
    var err = h('div', { class: 'errmsg hidden' });
    var btn = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, 'Войти');
    var form = h('form', { onsubmit: function (e) {
      e.preventDefault(); err.classList.add('hidden'); btn.disabled = true;
      SCC.api('/api/login', { json: { username: u.value, password: p.value }, quiet: true }).then(start, function (x) {
        btn.disabled = false; err.textContent = x.message; err.classList.remove('hidden'); p.value = ''; p.focus();
      });
    } },
      h('div', { class: 'Box-body' },
        h('div', { class: 'form-group' }, h('label', null, 'Имя пользователя'), u),
        h('div', { class: 'form-group' }, h('label', null, 'Пароль'), p), err, btn));
    authFrame('Вход в Server Control' + (b.name ? ' · ' + b.name : ''), h('div', { class: 'Box', style: 'border:0' }, form));
  }

  /* ====================================================== мастер первой настройки */
  function renderWizard(b) {
    var presetType = b.setup && b.setup.type_preset ? b.type : null;
    var w = { step: 0, setup_token: '', type: presetType || 'panel', name: (b.setup && b.setup.hostname) || '', username: 'admin', password: '', password2: '',
      update: { enabled: true, tag: 'v*', auto_apply: false, repo: 'Kikimorza/Server-Control-Web' } };
    var labels = ['Код установки', 'Тип', 'Учётная запись', 'Обновления', 'Готово'];
    var order = presetType ? [0, 2, 3, 4] : [0, 1, 2, 3, 4];
    var holder = h('div');
    authFrame('Первоначальная настройка', holder, true);
    var err = null;

    function steps() {
      var out = [];
      order.forEach(function (s, i) {
        if (i) out.push(h('span', { class: 'sep' }));
        var cls = 'step' + (s === w.step ? ' active' : order.indexOf(s) < order.indexOf(w.step) ? ' done' : '');
        out.push(h('span', { class: cls }, h('span', { class: 'n' }, order.indexOf(s) < order.indexOf(w.step) ? icon('check', 12) : String(i + 1)), h('span', { class: 'hide-sm' }, labels[s])));
      });
      return h('div', { class: 'Steps' }, out);
    }
    function field(label, el, hint) { return h('div', { class: 'form-group' }, h('label', null, label), el, hint ? h('div', { class: 'form-hint' }, hint) : null); }
    function bind(el, key, obj) { el.addEventListener('input', function () { (obj || w)[key] = el.type === 'checkbox' ? el.checked : el.value; }); return el; }
    function next() { var i = order.indexOf(w.step); w.step = order[Math.min(order.length - 1, i + 1)]; err = null; draw(); }
    function back() { var i = order.indexOf(w.step); w.step = order[Math.max(0, i - 1)]; err = null; draw(); }
    function errBox() { return err ? h('div', { class: 'errmsg' }, err) : null; }
    function nav(nextLabel, onNext, noBack) {
      return h('div', { class: 'form-actions', style: 'justify-content:space-between' },
        noBack ? h('span') : h('button', { class: 'btn', type: 'button', onclick: back }, 'Назад'),
        h('button', { class: 'btn btn-primary', type: 'button', onclick: onNext }, nextLabel));
    }

    function draw() {
      var body;
      if (w.step === 0) {
        var t = bind(h('input', { class: 'form-control mono', value: w.setup_token, placeholder: 'например, k3J9x_Qa2Lw1', autofocus: true }), 'setup_token');
        body = [h('h4', { class: 'mb-2' }, 'Добро пожаловать'),
          h('p', { class: 'muted' }, 'Чтобы убедиться, что настройку начинаете именно вы, введите код установки. Он выведен в консоли сервера при запуске и сохранён в файле data/setup_token.txt.'),
          field('Код установки', t), errBox(),
          nav('Продолжить', function () { if (!w.setup_token.trim()) { err = 'Введите код установки'; return draw(); } next(); }, true)];
        t.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); if (w.setup_token.trim()) next(); } });
      } else if (w.step === 1) {
        var opts = [['panel', 'server', 'Хостинг и нода управления', 'Хостит веб-панель, управляет собой и другими нодами. Для главного сервера.'],
          ['node', 'plug', 'Нода', 'Подчинённый сервер: им управляют из панели по токену. Веб-интерфейс остаётся доступным и локально.']];
        body = [h('h4', { class: 'mb-2' }, 'Тип этой установки'),
          h('p', { class: 'muted' }, 'Значение хранится в config.json (поле «type») - его можно задать заранее.'),
          opts.map(function (o) {
            return h('button', { class: 'radio-card' + (w.type === o[0] ? ' selected' : ''), type: 'button', onclick: function () { w.type = o[0]; draw(); } },
              icon(o[1], 20), h('span', null, h('strong', null, o[2]), h('span', { class: 'muted' }, o[3])));
          }), nav('Продолжить', next)];
      } else if (w.step === 2) {
        body = [h('h4', { class: 'mb-2' }, 'Имя и администратор'),
          field('Название сервера', bind(h('input', { class: 'form-control', value: w.name }), 'name'), 'Отображается в шапке и в списке нод.'),
          field('Имя администратора', bind(h('input', { class: 'form-control', value: w.username, autocomplete: 'username' }), 'username'), '3-32 символа: латиница, цифры, . _ -'),
          field('Пароль', bind(h('input', { class: 'form-control', type: 'password', value: w.password, autocomplete: 'new-password' }), 'password'), 'Минимум 8 символов.'),
          field('Повторите пароль', bind(h('input', { class: 'form-control', type: 'password', value: w.password2, autocomplete: 'new-password' }), 'password2')),
          errBox(), nav('Продолжить', function () {
            if (!/^[A-Za-z0-9_.-]{3,32}$/.test(w.username)) err = 'Недопустимое имя пользователя';
            else if (w.password.length < 8) err = 'Пароль слишком короткий (минимум 8 символов)';
            else if (w.password !== w.password2) err = 'Пароли не совпадают';
            else return next();
            draw();
          })];
      } else if (w.step === 3) {
        var u = w.update;
        body = [h('h4', { class: 'mb-2' }, 'Автообновление'),
          h('p', { class: 'muted' }, 'Обновления берутся из GitHub Releases. Параметры можно изменить позже в Настройки → Обновления.'),
          field('Репозиторий', bind(h('input', { class: 'form-control mono', value: u.repo }), 'repo', u)),
          field('Тег релиза', bind(h('input', { class: 'form-control mono', value: u.tag }), 'tag', u), 'Точный тег (например, stable) или маска (v* - самый новый из v1.x.x, v2.x.x…).'),
          h('div', { class: 'form-check' }, bind(h('input', { type: 'checkbox', id: 'u1', checked: u.enabled }), 'enabled', u), h('div', null, h('label', { for: 'u1' }, 'Проверять обновления автоматически'))),
          h('div', { class: 'form-check' }, bind(h('input', { type: 'checkbox', id: 'u2', checked: u.auto_apply }), 'auto_apply', u), h('div', null, h('label', { for: 'u2' }, 'Устанавливать автоматически'), h('div', { class: 'form-hint' }, 'Не рекомендуется: релиз выполняется на сервере с правами панели.'))),
          nav('Продолжить', next)];
      } else if (w.step === 4) {
        body = [h('h4', { class: 'mb-2' }, 'Проверьте и завершите'),
          h('div', { class: 'Box mb-3' },
            [['Тип', b.types[w.type]], ['Название', w.name || '-'], ['Администратор', w.username], ['Репозиторий обновлений', w.update.repo], ['Тег', w.update.tag],
              ['Автоустановка', w.update.auto_apply ? 'да' : 'нет']].map(function (r) {
              return h('div', { class: 'Box-row' }, h('span', { class: 'muted', style: 'width:200px' }, r[0]), h('strong', null, r[1]));
            })),
          w.type === 'node' ? SCC.flash('info', 'После завершения будет создан токен доступа для панели управления. Он показывается один раз.') : null,
          errBox(), nav('Завершить настройку', finish)];
      } else if (w.step === 5) {
        body = [h('div', { class: 'flash flash-success' }, icon('check-circle'), h('div', null, h('strong', null, 'Готово! '), 'Сервер настроен.')),
          w.node_token ? [h('h4', { class: 'mb-2' }, 'Токен доступа ноды'),
            h('p', { class: 'muted' }, 'Скопируйте его сейчас - повторно показать нельзя (только создать новый в Настройки → Доступ ноды). В панели управления: Ноды → Добавить ноду.'),
            h('div', { class: 'token-box mb-3' }, h('code', null, w.node_token), h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { SCC.copy(w.node_token); } }, icon('copy'), 'Копировать'))] : null,
          h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: start }, 'Открыть панель')];
      }
      clear(holder).appendChild(h('div', { class: 'Box' }, h('div', { class: 'Box-body' }, w.step < 5 ? steps() : null, body)));
    }
    function finish() {
      err = null;
      SCC.api('/api/setup', { quiet: true, json: { setup_token: w.setup_token.trim(), type: w.type, name: w.name, username: w.username, password: w.password, update: w.update } })
        .then(function (r) { w.node_token = r.node_token; w.step = 5; draw(); }, function (e) {
          err = e.message; if (e.status === 403) { w.step = 0; } draw();
        });
    }
    draw();
  }

  /* ====================================================== оболочка */
  var shell = {};
  function isLocal() { return SCC.state.node === 'local'; }
  function navItems() {
    var items = (SCC.state.boot.nav || []).concat(SCC.extraNav);
    var mods = SCC.state.nodeModules;
    if (!isLocal() && mods) items = items.filter(function (n) { return !n.module || mods.indexOf(n.module) >= 0; });
    return items.filter(function (n) { return SCC.pages[n.page]; }).sort(function (a, b) { return (a.order || 100) - (b.order || 100); });
  }
  function nodeLabel() {
    if (isLocal()) return SCC.state.boot.name || SCC.state.boot.hostname || 'localhost';
    var n = (SCC.state.nodes || []).filter(function (x) { return x.id === SCC.state.node; })[0];
    return n ? n.name : SCC.state.node;
  }
  function renderShell() {
    var b = SCC.state.boot, u = b.user;
    var navEl = h('nav', { class: 'UnderlineNav', 'aria-label': 'Разделы' });
    var bannerEl = h('div');
    var headEl = h('div', { class: 'PageHead' });
    var main = h('main', { id: 'main' });

    var nodeBtn = h('button', { class: 'node-btn', type: 'button' }, h('span', { id: 'node-name' }, nodeLabel()), SCC.state.nodes ? icon('chevron-down', 12) : null);
    var crumb = h('div', { class: 'Breadcrumb' },
      h('a', { href: '#/settings/accounts', class: 'hide-sm' }, u.name), h('span', { class: 'sep hide-sm' }, '/'),
      SCC.state.nodes ? SCC.menu(nodeBtn, function () {
        var list = [{ header: 'Выберите ноду' }, { label: (b.name || b.hostname) + ' (эта панель)', icon: 'server', active: isLocal(), onclick: function () { switchNode('local'); } }];
        (SCC.state.nodes || []).forEach(function (n) { list.push({ label: n.name, icon: 'globe', active: SCC.state.node === n.id, onclick: function () { switchNode(n.id); } }); });
        list.push({ divider: true }, { label: 'Управление нодами…', icon: 'gear', onclick: function () { SCC.state.node = 'local'; location.hash = '#/nodes'; } });
        return list;
      }, { left: true }) : h('strong', null, nodeLabel()));

    var search = h('div', { class: 'HeaderSearch' }, icon('search'), h('input', { id: 'gsearch', type: 'text', placeholder: 'Перейти к…', autocomplete: 'off', 'aria-label': 'Поиск по разделам' }), h('kbd', null, '/'));
    var results = null;
    var inp = $('input', search);
    inp.addEventListener('input', function () {
      if (results) { results.remove(); results = null; }
      var q = inp.value.trim().toLowerCase(); if (!q) return;
      var all = navItems().map(function (n) { return { t: n.title, ic: n.icon, go: function () { SCC.go(n.page); } }; });
      all.push({ t: 'Настройки', ic: 'gear', go: function () { SCC.go('settings'); } });
      SCC.settingsTabs.concat(coreTabs()).forEach(function (t) { all.push({ t: 'Настройки · ' + t.title, ic: t.icon || 'gear', go: function () { SCC.go('settings', t.id); } }); });
      (SCC.state.nodes || []).forEach(function (n) { all.push({ t: 'Нода · ' + n.name, ic: 'globe', go: function () { switchNode(n.id); } }); });
      var found = all.filter(function (x) { return x.t.toLowerCase().indexOf(q) >= 0; }).slice(0, 8);
      results = h('div', { class: 'search-results' }, found.length ? found.map(function (x) {
        return h('button', { class: 'dropdown-item', type: 'button', onmousedown: function (e) { e.preventDefault(); inp.value = ''; results.remove(); results = null; x.go(); } }, icon(x.ic || 'file'), x.t);
      }) : h('div', { class: 'dropdown-header' }, 'Ничего не найдено'));
      search.appendChild(results);
    });
    inp.addEventListener('blur', function () { setTimeout(function () { if (results) { results.remove(); results = null; } }, 150); });
    document.addEventListener('keydown', function (e) {
      if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) { e.preventDefault(); inp.focus(); }
    });

    var plus = SCC.menu(h('button', { class: 'IconBtn', type: 'button', 'aria-label': 'Создать' }, icon('plus'), icon('chevron-down', 10)), function () {
      var items = [{ header: 'Создать' }];
      if (SCC.pages.terminal) items.push({ label: 'Открыть терминал', icon: 'terminal', onclick: function () { SCC.go('terminal'); } });
      if (SCC.pages.nodes && SCC.isAdmin()) items.push({ label: 'Добавить ноду', icon: 'server', onclick: function () { SCC.state.node = 'local'; location.hash = '#/nodes/add'; } });
      if (SCC.isAdmin()) items.push({ label: 'Установить модуль', icon: 'package', onclick: function () { SCC.go('settings', 'modules', 'install'); } });
      return items.concat(SCC.menuPlus);
    });
    var updDot = b.update && b.update.available;
    var updBtn = h('button', { class: 'IconBtn', type: 'button', title: 'Обновления', 'aria-label': 'Обновления', onclick: function () { SCC.go('settings', 'updates'); } }, icon('inbox'), updDot ? h('span', { class: 'dot' }) : null);
    var av = h('button', { class: 'Avatar', type: 'button', 'aria-label': 'Меню пользователя' }, u.name.charAt(0));
    var avMenu = SCC.menu(av, function () {
      var th = SCC.theme();
      return [{ header: 'Вы вошли как ' + u.name + (u.role === 'admin' ? '' : ' (просмотр)') },
        { label: 'Аккаунт', icon: 'person', onclick: function () { location.hash = '#/settings/accounts'; } },
        SCC.isAdmin() ? { label: 'Настройки', icon: 'gear', onclick: function () { SCC.go('settings'); } } : null,
        { divider: true },
        { label: 'Тема: ' + (th === 'auto' ? 'как в системе' : th === 'dark' ? 'тёмная' : 'светлая') + ' (сменить)', icon: th === 'dark' ? 'moon' : 'sun', onclick: function () { SCC.theme(th === 'auto' ? 'light' : th === 'light' ? 'dark' : 'auto'); } },
        { divider: true },
        { label: 'Выйти', icon: 'sign-out', danger: true, onclick: function () { SCC.api('/api/logout', { method: 'POST', json: {} }).then(function () { SCC.state.user = null; SCC.state.csrf = null; start(); }); } }];
    });
    var menuBtn = SCC.menu(h('button', { class: 'IconBtn menu-btn', type: 'button', 'aria-label': 'Меню' }, icon('menu')), function () {
      return navItems().map(function (n) { return { label: n.title, icon: n.icon, onclick: function () { SCC.go(n.page); } }; }).concat([{ divider: true }, { label: 'Настройки', icon: 'gear', onclick: function () { SCC.go('settings'); } }]);
    }, { left: true });
    var rightExtra = h('span', { class: 'flex gap-2 items-center' });
    var header = h('header', { class: 'AppHeader' },
      h('div', { class: 'AppHeader-top' },
        h('div', { class: 'AppHeader-left' }, menuBtn, h('a', { class: 'AppHeader-logo', href: '#/', 'aria-label': 'Главная' }, h('img', { src: '/favicon.svg', alt: '' })), crumb),
        h('div', { class: 'AppHeader-right' }, search, rightExtra, plus, updBtn, avMenu)),
      navEl);
    SCC.mounts['header-right'].forEach(function (e) { rightExtra.appendChild(e); });
    clear(root).appendChild(h('div', null, header, bannerEl, h('div', { class: 'Container' }, headEl, main)));
    shell = { nav: navEl, banner: bannerEl, head: headEl, main: main, nodeName: crumb, bannerEl: bannerEl };
    renderBanner();
  }

  function renderBanner() {
    var b = SCC.state.boot, up = b.update, el = shell.banner; clear(el);
    var key = 'scc-upd-dismissed';
    var dismissed; try { dismissed = sessionStorage.getItem(key); } catch (e) { }
    if (up && up.available && SCC.isAdmin() && dismissed !== up.tag) {
      el.appendChild(h('div', { class: 'Banner warning', role: 'alert' }, icon('alert'),
        h('div', { class: 'body' }, 'Доступна новая версия ', h('strong', null, up.latest), ' (установлена ' + up.current + '). Обновление загружается из GitHub Releases.'),
        h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { SCC.go('settings', 'updates'); } }, 'Обновить'),
        h('button', { class: 'close', type: 'button', 'aria-label': 'Скрыть', onclick: function () { try { sessionStorage.setItem(key, up.tag); } catch (e) { } clear(el); } }, icon('x'))));
    }
  }

  function switchNode(id) {
    SCC.state.node = id; SCC.state.nodeInfo = null; SCC.state.nodeModules = null;
    var h0 = parseHash(); location.hash = SCC.link(h0.page || '');
  }

  function parseHash() {
    var p = location.hash.replace(/^#\/?/, ''), qs = '';
    var qi = p.indexOf('?'); if (qi >= 0) { qs = p.slice(qi + 1); p = p.slice(0, qi); }
    var parts = p.split('/').filter(Boolean).map(function (x) { try { return decodeURIComponent(x); } catch (e) { return x; } });
    var node = 'local';
    if (parts[0] === 'n' && parts.length >= 2) { node = parts[1]; parts = parts.slice(2); }
    return { node: node, page: parts[0] || null, args: parts.slice(1), query: new URLSearchParams(qs) };
  }

  /* ====================================================== роутер */
  var routeSeq = 0;
  function route() {
    var seq = ++routeSeq, r = parseHash();
    SCC._cleanups.splice(0).forEach(function (f) { try { f(); } catch (e) { console.error(e); } });
    if (r.node !== SCC.state.node) { SCC.state.node = r.node; SCC.state.nodeInfo = null; SCC.state.nodeModules = null; }
    var prep = Promise.resolve();
    if (!isLocal() && !SCC.state.nodeModules) {
      prep = Promise.all([SCC.api(SCC.url('/api/agent/info'), { quiet: true }), SCC.api(SCC.url('/api/modules'), { quiet: true })]).then(function (x) {
        SCC.state.nodeInfo = x[0]; SCC.state.nodeModules = x[1].modules.filter(function (m) { return m.loaded; }).map(function (m) { return m.id; });
      }).catch(function (e) { SCC.state.nodeError = e.message; SCC.state.nodeModules = null; return 'fail'; });
    }
    prep.then(function (fail) {
      if (seq !== routeSeq) return;
      var items = navItems();
      var page = r.page;
      if (!page) { page = items.length ? items[0].page : 'settings'; }
      drawNav(items, page); drawHead(); renderBanner();
      var main = clear(shell.main);
      if (fail === 'fail') {
        main.appendChild(h('div', { class: 'Page' }, SCC.blank('x-circle', 'Нода недоступна', SCC.state.nodeError || 'Нет связи с нодой.',
          h('button', { class: 'btn', type: 'button', onclick: function () { switchNode('local'); } }, 'Вернуться к этой панели'))));
        return;
      }
      var ctx = { args: r.args, query: r.query, node: SCC.state.node, go: SCC.go, link: SCC.link, onLeave: function (f) { SCC._cleanups.push(f); },
        url: SCC.url, api: SCC.api, mod: SCC.mod, refresh: route, isSeq: function () { return seq === routeSeq; } };
      var def = page === 'settings' ? { render: renderSettings } : SCC.pages[page];
      var wrap = h('div', { class: 'Page' });
      main.appendChild(wrap);
      if (!def) { wrap.appendChild(SCC.blank('file', 'Страница не найдена', 'Раздел «' + page + '» недоступен (модуль выключен или не установлен).')); return; }
      try {
        var out = def.render(wrap, ctx);
        if (out && typeof out.then === 'function') out.catch(function (e) { clear(wrap).appendChild(SCC.flash('error', 'Ошибка: ' + e.message)); });
        else if (typeof out === 'function') ctx.onLeave(out);
        if (out && typeof out.then === 'function') out.then(function (f) { if (typeof f === 'function') { if (seq === routeSeq) ctx.onLeave(f); else f(); } });
      } catch (e) { console.error(e); wrap.appendChild(SCC.flash('error', 'Ошибка отрисовки: ' + e.message)); }
      window.scrollTo(0, 0);
      SCC.emit('route', { page: page, args: r.args, node: SCC.state.node });
    });
  }

  function drawNav(items, current) {
    var el = clear(shell.nav);
    items.forEach(function (n) {
      el.appendChild(h('a', { class: 'UnderlineNav-item item-inner', href: SCC.link(n.page), 'aria-current': n.page === current ? 'page' : null },
        h('span', { class: 'label-text' }, icon(n.icon || 'package'), h('span', null, n.title), n.badge != null ? h('span', { class: 'Counter' }, n.badge) : null)));
    });
    if (SCC.isAdmin() || isLocal()) el.appendChild(h('a', { class: 'UnderlineNav-item item-inner', href: SCC.link('settings'), 'aria-current': current === 'settings' ? 'page' : null },
      h('span', { class: 'label-text' }, icon('gear'), h('span', null, 'Настройки'))));
    if (shell.nodeName) { var nn = $('#node-name'); if (nn) nn.textContent = nodeLabel(); }
  }

  function drawHead() {
    var b = SCC.state.boot, el = clear(shell.head);
    var info = SCC.state.nodeInfo;
    var title = isLocal() ? (b.name || b.hostname) : nodeLabel();
    var typ = isLocal() ? b.type_label : (info ? info.type === 'panel' ? b.types.panel : b.types.node : '');
    el.appendChild(h('span', { class: 'Avatar sm', style: 'background:var(--success-btn);color:#fff;border:0' }, icon('server', 12)));
    el.appendChild(h('h1', null, title));
    if (typ) el.appendChild(h('span', { class: 'Label' }, typ));
    if (!isLocal()) el.appendChild(h('span', { class: 'Label Label--accent' }, 'удалённая нода'));
    var act = h('div', { class: 'actions' });
    SCC.mounts['page-actions'].forEach(function (e) { act.appendChild(e); });
    if (SCC.isAdmin()) {
      act.appendChild(h('button', { class: 'btn btn-sm', type: 'button', onclick: function () {
        SCC.confirm('Перезапустить сервис?', 'Сервис «' + title + '» будет перезапущен. Интерфейс на время станет недоступен.', { ok: 'Перезапустить', danger: true }).then(function (ok) {
          if (ok) SCC.api(SCC.url('/api/system/restart'), { method: 'POST', json: {} }).then(function () { SCC.toast('Перезапуск…', 'info'); waitForServer(); }, SCC.err);
        });
      } }, icon('power', 14), 'Перезапуск'));
      act.appendChild(h('button', { class: 'btn btn-sm', type: 'button', onclick: function (e) {
        var btn = e.currentTarget; SCC.busy(btn, function () {
          return SCC.api(SCC.url('/api/update/check'), { method: 'POST', json: {} }).then(function (s) {
            if (isLocal()) { b.update = { available: s.available, latest: s.latest, tag: s.tag, current: s.current }; renderBanner(); }
            SCC.toast(s.error ? s.error : s.available ? 'Доступна версия ' + s.latest : 'Установлена последняя версия', s.error ? 'error' : 'success');
          });
        });
      } }, icon('sync', 14), 'Обновления', h('span', { class: 'Counter' }, b.version)));
    }
    el.appendChild(act);
  }

  function waitForServer(oldVersion) {
    var tries = 0;
    (function poll() {
      setTimeout(function () {
        fetch('/api/boot', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
          if (j && j.version && (tries > 1 || j.version !== oldVersion)) location.reload(); else { tries++; poll(); }
        }).catch(function () { tries++; poll(); });
      }, 1200);
    })();
  }
  SCC.waitForServer = waitForServer;

  /* ====================================================== настройки */
  function coreTabs() {
    var t = [{ id: 'general', title: 'Общие', icon: 'gear', render: tabGeneral },
      { id: 'accounts', title: 'Учётные записи', icon: 'person', render: tabAccounts }];
    if (SCC.isAdmin()) {
      t.push({ id: 'updates', title: 'Обновления', icon: 'sync', render: tabUpdates },
        { id: 'modules', title: 'Модули', icon: 'package', render: tabModules },
        { id: 'node-access', title: 'Доступ ноды', icon: 'key', render: tabNodeAccess });
    }
    return t;
  }
  function renderSettings(rootEl, ctx) {
    var tabs = coreTabs().concat(SCC.settingsTabs), cur = ctx.args[0] || 'general';
    var tab = tabs.filter(function (t) { return t.id === cur; })[0] || tabs[0];
    var side = h('ul', { class: 'SideNav' }, tabs.map(function (t) {
      return h('li', null, h('a', { href: SCC.link('settings', t.id), class: t.id === tab.id ? 'current' : '' }, icon(t.icon || 'gear'), t.title));
    }));
    var content = h('div', { class: 'flex-1' });
    rootEl.appendChild(h('div', { class: 'Container', style: 'padding:0' }, h('div', { class: 'SettingsLayout' }, h('aside', null, side), content)));
    content.appendChild(h('div', { class: 'Subhead' }, h('h2', null, tab.title)));
    var body = h('div'); content.appendChild(body);
    var sub = Object.assign({}, ctx, { args: ctx.args.slice(1) });
    var out = tab.render(body, sub);
    if (out && out.catch) out.catch(function (e) { body.appendChild(SCC.flash('error', e.message)); });
    return out;
  }

  function field(label, el, hint) { return h('div', { class: 'form-group' }, h('label', null, label), el, hint ? h('div', { class: 'form-hint' }, hint) : null); }
  function check(id, label, checked, hint) {
    var cb = h('input', { type: 'checkbox', id: id, checked: !!checked });
    return { el: h('div', { class: 'form-check' }, cb, h('div', null, h('label', { for: id }, label), hint ? h('div', { class: 'form-hint' }, hint) : null)), cb: cb };
  }

  function tabGeneral(el) {
    return SCC.api(SCC.url('/api/settings')).then(function (s) {
      var name = h('input', { class: 'form-control', value: s.name || '' });
      var type = h('select', { class: 'form-control' }, Object.keys(s.types).map(function (k) { return h('option', { value: k }, s.types[k]); })); type.value = s.type;
      var host = h('input', { class: 'form-control mono', value: s.host }), port = h('input', { class: 'form-control mono', type: 'number', min: 1, max: 65535, value: s.port });
      var sess = h('input', { class: 'form-control', type: 'number', step: '0.5', min: 0.5, value: s.session_hours });
      var tp = check('tp', 'Доверять заголовку X-Forwarded-For', s.trust_proxy, 'Включайте, только если панель стоит за вашим reverse proxy (nginx, Caddy).');
      var msg = h('div');
      var save = h('button', { class: 'btn btn-primary', type: 'button', onclick: function () {
        SCC.busy(save, function () {
          return SCC.api(SCC.url('/api/settings'), { json: { name: name.value, type: type.value, host: host.value, port: port.value, session_hours: sess.value, trust_proxy: tp.cb.checked } }).then(function (r) {
            clear(msg);
            if (r.restart_required) msg.appendChild(SCC.flash('warn', 'Для применения типа, адреса или порта нужен перезапуск.', h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { SCC.api(SCC.url('/api/system/restart'), { method: 'POST', json: {} }).then(function () { SCC.toast('Перезапуск…'); waitForServer(); }); } }, 'Перезапустить сейчас')));
            else SCC.toast('Сохранено', 'success');
          });
        });
      } }, 'Сохранить');
      el.appendChild(h('div', { style: 'max-width:540px' }, msg,
        field('Название сервера', name), field('Тип установки', type, 'Хранится в config.json → type. «Хостинг и нода управления» хостит панель и управляет нодами.'),
        h('div', { class: 'flex gap-2' }, h('div', { class: 'flex-1' }, field('Адрес прослушивания', host, '0.0.0.0 - все интерфейсы, 127.0.0.1 - только локально')), h('div', { style: 'width:140px' }, field('Порт', port))),
        field('Длительность сессии (часы)', sess), tp.el, h('div', { class: 'form-actions' }, save),
        h('hr'), h('p', { class: 'muted' }, 'Файл конфигурации: ', h('code', null, s.config_path), h('br'), 'Каталог данных: ', h('code', null, s.data_dir), h('br'), 'TLS: ' + (s.tls ? 'включён' : 'выключен (укажите tls.cert и tls.key в config.json или используйте reverse proxy)'))));
    });
  }

  function tabAccounts(el) {
    var me = SCC.state.user;
    if (isLocal()) {
      var o = h('input', { class: 'form-control', type: 'password', autocomplete: 'current-password' }), n1 = h('input', { class: 'form-control', type: 'password', autocomplete: 'new-password' }), n2 = h('input', { class: 'form-control', type: 'password', autocomplete: 'new-password' });
      var ch = h('button', { class: 'btn btn-primary', type: 'button', onclick: function () {
        if (n1.value !== n2.value) return SCC.toast('Пароли не совпадают', 'error');
        SCC.busy(ch, function () { return SCC.api('/api/account/password', { json: { old: o.value, new: n1.value } }).then(function () { o.value = n1.value = n2.value = ''; SCC.toast('Пароль изменён', 'success'); }); });
      } }, 'Сменить пароль');
      el.appendChild(h('div', { class: 'Box mb-3', style: 'max-width:540px' }, h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('lock'), 'Мой пароль (' + me.name + ')')),
        h('div', { class: 'Box-body' }, field('Текущий пароль', o), field('Новый пароль', n1, 'Минимум 8 символов'), field('Повторите новый пароль', n2), ch)));
    }
    if (!SCC.isAdmin()) return;
    var list = h('div', { class: 'Box' });
    function load() {
      return SCC.api(SCC.url('/api/users')).then(function (r) {
        clear(list).appendChild(h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('people'), 'Пользователи', h('span', { class: 'Counter' }, r.users.length)),
          h('button', { class: 'btn btn-sm btn-primary ml-auto', type: 'button', onclick: addUser }, icon('plus', 14), 'Добавить')));
        r.users.forEach(function (u) {
          var isMe = isLocal() && u.name === me.name;
          var role = h('select', { class: 'form-control sm', style: 'width:160px', disabled: isMe, onchange: function () { SCC.api(SCC.url('/api/users/' + encodeURIComponent(u.name) + '/role'), { json: { role: role.value } }).then(function () { SCC.toast('Роль изменена', 'success'); }, function (e) { SCC.err(e); load(); }); } },
            h('option', { value: 'admin' }, 'Администратор'), h('option', { value: 'viewer' }, 'Просмотр')); role.value = u.role;
          list.appendChild(h('div', { class: 'Box-row' }, h('span', { class: 'Avatar sm' }, u.name.charAt(0)), h('strong', null, u.name), isMe ? h('span', { class: 'Label' }, 'вы') : null,
            h('span', { class: 'muted flex-1' }, 'создан ' + fmt.ago(u.created)), role,
            h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { SCC.prompt('Новый пароль для ' + u.name, 'Пароль (минимум 8 символов)', { type: 'password' }).then(function (p) { if (p) SCC.api(SCC.url('/api/users/' + encodeURIComponent(u.name) + '/password'), { json: { password: p } }).then(function () { SCC.toast('Пароль изменён, сессии пользователя завершены', 'success'); }, SCC.err); }); } }, icon('key', 14), 'Пароль'),
            h('button', { class: 'btn btn-sm btn-danger', type: 'button', disabled: isMe, onclick: function () { SCC.confirm('Удалить пользователя?', 'Пользователь «' + u.name + '» потеряет доступ.', { danger: true, ok: 'Удалить' }).then(function (ok) { if (ok) SCC.api(SCC.url('/api/users/' + encodeURIComponent(u.name)), { method: 'DELETE' }).then(load, SCC.err); }); } }, icon('trash', 14))));
        });
      });
    }
    function addUser() {
      var un = h('input', { class: 'form-control', autocomplete: 'off' }), pw = h('input', { class: 'form-control', type: 'password', autocomplete: 'new-password' });
      var rl = h('select', { class: 'form-control' }, h('option', { value: 'viewer' }, 'Просмотр (только чтение)'), h('option', { value: 'admin' }, 'Администратор'));
      SCC.dialog({ title: 'Новый пользователь', body: h('div', null, field('Имя', un), field('Пароль', pw, 'Минимум 8 символов'), field('Роль', rl)),
        actions: [{ label: 'Отмена' }, { label: 'Создать', kind: 'primary', onclick: function (close) { SCC.api(SCC.url('/api/users'), { json: { username: un.value, password: pw.value, role: rl.value } }).then(function () { close(); load(); }, SCC.err); } }] });
    }
    el.appendChild(h('div', { style: 'max-width:820px' }, list));
    return load();
  }

  function tabUpdates(el) {
    return SCC.api(SCC.url('/api/settings')).then(function (s) {
      var u = s.update, box = h('div'), poll = null;
      var repo = h('input', { class: 'form-control mono', value: u.repo }), tag = h('input', { class: 'form-control mono', value: u.tag }), asset = h('input', { class: 'form-control mono', value: u.asset });
      var iv = h('input', { class: 'form-control', type: 'number', step: '0.5', min: 0.1, value: u.check_interval_hours });
      var tok = h('input', { class: 'form-control mono', type: 'password', placeholder: s.update_token_set ? '•••••• (задан, оставьте пустым чтобы не менять)' : 'необязательно, для приватных репозиториев', autocomplete: 'off' });
      var cEn = check('c1', 'Проверять обновления автоматически', u.enabled), cAuto = check('c2', 'Устанавливать автоматически', u.auto_apply, 'Новый релиз будет установлен без подтверждения, сервис перезапустится.');
      var cPre = check('c3', 'Учитывать pre-release', u.allow_prerelease), cSum = check('c4', 'Требовать SHA-256 в релизе', u.require_checksum, 'Отклонять релизы без контрольной суммы (digest в asset или файл *.sha256).');
      function view(st) {
        clear(box);
        var rows = [['Установлена', st.current + (st.installed_tag ? '  (тег ' + st.installed_tag + ')' : '')],
          ['Доступна', st.latest ? st.latest + '  (тег ' + st.tag + ')' : '-'], ['Проверено', st.checked ? fmt.date(st.checked) : 'ещё не проверялось']];
        var head = st.busy ? SCC.flash('info', h('span', null, h('strong', null, { download: 'Загрузка', extract: 'Распаковка', install: 'Установка файлов', restart: 'Перезапуск' }[st.phase] || 'Работаю'), st.phase === 'download' && st.total ? ' ' + fmt.bytes(st.done) + ' из ' + fmt.bytes(st.total) : '…'))
          : st.error ? SCC.flash('error', st.error) : st.available ? SCC.flash('warn', 'Доступна новая версия ' + st.latest) : st.checked ? SCC.flash('success', 'Установлена актуальная версия') : null;
        box.appendChild(h('div', { class: 'Box mb-3' }, h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('tag'), 'Состояние'),
          h('span', { class: 'ml-auto flex gap-2' },
            h('button', { class: 'btn btn-sm', type: 'button', disabled: st.busy, onclick: function (e) { SCC.busy(e.currentTarget, function () { return SCC.api(SCC.url('/api/update/check'), { method: 'POST', json: {} }).then(function (x) { view(x); if (isLocal()) { SCC.state.boot.update = { available: x.available, latest: x.latest, tag: x.tag, current: x.current }; renderBanner(); } }); }); } }, icon('sync', 14), 'Проверить'),
            st.available && !st.busy ? h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: apply }, icon('download', 14), 'Установить ' + st.latest) : null)),
          h('div', { class: 'Box-body' }, head, st.busy && st.total ? h('div', { class: 'Progress mb-3' }, h('span', { style: 'width:' + Math.round(st.done / st.total * 100) + '%' })) : null,
            rows.map(function (r) { return h('div', { class: 'flex mb-2' }, h('span', { class: 'muted', style: 'width:140px' }, r[0]), h('strong', null, r[1])); }),
            st.verified === true ? h('div', { class: 'success' }, icon('shield', 14), ' SHA-256 проверена') : null,
            st.notes ? h('details', { class: 'mt-3' }, h('summary', null, 'Описание релиза'), h('pre', { class: 'code mt-2' }, st.notes)) : null)));
      }
      function apply() {
        SCC.confirm('Установить обновление?', 'Файлы панели будут заменены, а сервис перезапущен. Настройки, данные и ваши модули сохранятся; резервная копия будет в data/backup.', { ok: 'Установить' }).then(function (ok) {
          if (!ok) return;
          var old = s && SCC.state.boot.version;
          SCC.api(SCC.url('/api/update/apply'), { method: 'POST', json: {} }).then(function () {
            poll = setInterval(function () {
              SCC.api(SCC.url('/api/update/status'), { quiet: true }).then(function (st) {
                view(st);
                if (st.phase === 'restart') { clearInterval(poll); box.appendChild(SCC.flash('info', 'Перезапуск… страница обновится автоматически.')); waitForServer(old); }
                if (!st.busy && st.error) clearInterval(poll);
              }).catch(function () { clearInterval(poll); waitForServer(old); });
            }, 700);
          }, SCC.err);
        });
      }
      SCC._cleanups.push(function () { if (poll) clearInterval(poll); });
      var save = h('button', { class: 'btn btn-primary', type: 'button', onclick: function () {
        var up = { repo: repo.value, tag: tag.value, asset: asset.value, check_interval_hours: iv.value, enabled: cEn.cb.checked, auto_apply: cAuto.cb.checked, allow_prerelease: cPre.cb.checked, require_checksum: cSum.cb.checked };
        if (tok.value) up.token = tok.value;
        SCC.busy(save, function () { return SCC.api(SCC.url('/api/settings'), { json: { update: up } }).then(function () { SCC.toast('Сохранено', 'success'); tok.value = ''; }); });
      } }, 'Сохранить настройки');
      el.appendChild(h('div', { style: 'max-width:720px' }, box,
        h('div', { class: 'Box' }, h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('gear'), 'Источник обновлений (GitHub Releases)')),
          h('div', { class: 'Box-body' },
            field('Репозиторий', repo, 'Формат владелец/репозиторий'),
            field('Тег релиза', tag, 'Точный тег (stable) или маска со * (v*, v1.*). Берётся самый новый подходящий релиз.'),
            field('Имя файла (asset)', asset, 'Маска zip-файла в релизе. Если такого нет, будет использован архив исходников тега.'),
            field('Интервал проверки (часы)', iv), field('GitHub token', tok), cEn.el, cAuto.el, cPre.el, cSum.el, h('div', { class: 'form-actions' }, save)))));
      return SCC.api(SCC.url('/api/update/status')).then(view);
    });
  }

  function tabModules(el, ctx) {
    var list = h('div', { class: 'Box' });
    function load() {
      return SCC.api(SCC.url('/api/modules')).then(function (r) {
        clear(list).appendChild(h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('package'), 'Установленные модули', h('span', { class: 'Counter' }, r.modules.length)),
          h('button', { class: 'btn btn-sm btn-primary ml-auto', type: 'button', onclick: install }, icon('upload', 14), 'Установить модуль')));
        if (!r.modules.length) list.appendChild(SCC.blank('package', 'Модулей нет', 'Положите папку в modules/ или установите zip.'));
        r.modules.forEach(function (m) {
          var state = m.error ? h('span', { class: 'Label Label--danger', title: m.error }, 'Ошибка') : m.loaded ? h('span', { class: 'Label Label--success' }, 'Работает') : !m.compatible ? h('span', { class: 'Label Label--attention', title: m.reason }, 'Несовместим') : h('span', { class: 'Label' }, m.enabled ? 'Не загружен' : 'Выключен');
          list.appendChild(h('div', { class: 'Box-row', style: 'align-items:flex-start;padding:12px 16px' }, h('span', { class: 'icon-col', style: 'margin-top:3px' }, icon(m.icon || 'package')),
            h('div', { class: 'flex-1' }, h('div', { class: 'flex items-center gap-2 wrap' }, h('strong', null, m.name), h('span', { class: 'muted' }, m.version), state, m.builtin ? h('span', { class: 'Label' }, 'встроенный') : null,
              h('span', { class: 'Label' }, m.roles.length === 2 ? 'панель и нода' : m.roles[0] === 'panel' ? 'только панель' : 'только нода')),
              h('div', { class: 'muted' }, m.description || ''), m.error ? h('div', { class: 'danger', style: 'font-size:12px' }, m.error) : (!m.compatible ? h('div', { class: 'attention', style: 'font-size:12px' }, m.reason) : null)),
            h('div', { class: 'flex gap-2' },
              h('button', { class: 'btn btn-sm', type: 'button', disabled: !m.compatible && !m.enabled, onclick: function (e) { SCC.busy(e.currentTarget, function () { return SCC.api(SCC.url('/api/modules/' + m.id + '/' + (m.enabled ? 'disable' : 'enable')), { method: 'POST', json: {} }).then(function (x) { if (x.error) SCC.toast(x.error, 'error'); else SCC.toast(m.enabled ? 'Модуль выключен' : 'Модуль включён', 'success'); return load().then(refreshHint); }); }); } }, m.enabled ? 'Выключить' : 'Включить'),
              !m.builtin ? h('button', { class: 'btn btn-sm btn-danger', type: 'button', onclick: function () { SCC.confirm('Удалить модуль «' + m.name + '»?', 'Папка модуля будет удалена с диска.', { danger: true, ok: 'Удалить' }).then(function (ok) { if (ok) SCC.api(SCC.url('/api/modules/' + m.id), { method: 'DELETE' }).then(load, SCC.err); }); } }, icon('trash', 14)) : null)));
        });
      });
    }
    var hint = h('div');
    function refreshHint() { clear(hint).appendChild(SCC.flash('info', 'Изменения в меню и страницах применяются после обновления страницы.', h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { if (isLocal()) { start(); } else location.reload(); } }, 'Обновить'))); }
    function install() {
      var file = h('input', { type: 'file', accept: '.zip', class: 'form-control' }), url = h('input', { class: 'form-control mono', placeholder: 'https://github.com/…/releases/download/…/module.zip' });
      SCC.dialog({ title: 'Установить модуль', size: 'wide', body: h('div', null,
        SCC.flash('warn', 'Модуль выполняется внутри панели с теми же правами, что у сервиса, и может менять всё: сайт, файлы, систему. Устанавливайте только то, чему доверяете. Установленный модуль выключен, пока вы не включите его.'),
        field('Загрузить .zip', file, 'В архиве должен быть module.json'), h('div', { class: 'muted mb-2' }, 'или'), field('Скачать по ссылке', url)),
        actions: [{ label: 'Отмена' }, { label: 'Установить', kind: 'primary', onclick: function (close, btn) {
          var req;
          if (file.files[0]) req = SCC.api(SCC.url('/api/modules/install'), { method: 'POST', body: file.files[0], type: 'application/zip' });
          else if (url.value.trim()) req = SCC.api(SCC.url('/api/modules/install?url=' + encodeURIComponent(url.value.trim())), { method: 'POST', json: {} });
          else return SCC.toast('Выберите файл или укажите ссылку', 'error');
          SCC.busy(btn, function () { return req.then(function (r) { close(); SCC.toast('Модуль «' + r.id + '» установлен (выключен)', 'success'); return load(); }); });
        } }] });
    }
    el.appendChild(h('div', { style: 'max-width:900px' }, hint, list));
    if (ctx.args[0] === 'install') setTimeout(install, 50);
    return load();
  }

  function tabNodeAccess(el) {
    return SCC.api(SCC.url('/api/settings')).then(function (s) {
      var out = h('div');
      var btn = h('button', { class: 'btn ' + (s.node_token_set ? '' : 'btn-primary'), type: 'button', onclick: function () {
        SCC.confirm(s.node_token_set ? 'Создать новый токен?' : 'Создать токен?', s.node_token_set ? 'Старый токен перестанет работать, панели управления придётся указать новый.' : 'Токен позволяет панели управления управлять этим сервером.', { ok: 'Создать' }).then(function (ok) {
          if (!ok) return;
          SCC.api(SCC.url('/api/settings/node-token'), { method: 'POST', json: {} }).then(function (r) {
            clear(out).appendChild(h('div', null, SCC.flash('success', 'Токен создан. Он показывается только сейчас.'),
              h('div', { class: 'token-box' }, h('code', null, r.token), h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { SCC.copy(r.token); } }, icon('copy'), 'Копировать'))));
            s.node_token_set = true; btn.textContent = 'Пересоздать токен';
          }, SCC.err);
        });
      } }, s.node_token_set ? 'Пересоздать токен' : 'Создать токен');
      el.appendChild(h('div', { style: 'max-width:640px' }, h('p', { class: 'muted' }, 'Панель управления подключается к этому серверу по адресу ', h('code', null, location.protocol + '//' + location.host), ' с помощью токена. Токен даёт полный административный доступ, храните его как пароль. На сервере хранится только его SHA-256.'),
        h('div', { class: 'mb-3' }, 'Статус: ', s.node_token_set ? h('span', { class: 'Label Label--success' }, 'токен создан') : h('span', { class: 'Label' }, 'токена нет')), btn, h('div', { class: 'mt-3' }, out)));
    });
  }

  /* ====================================================== поехали */
  start();
})();
