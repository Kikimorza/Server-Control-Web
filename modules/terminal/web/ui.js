(function () {
  var h = SCC.h, icon = SCC.icon;
  var HIST = []; var buf = {}; // буфер вывода по нодам
  SCC.registerPage('terminal', {
    render: function (el, ctx) {
      var api = SCC.mod('terminal'), key = ctx.node, hi = HIST.length, cwd = '~';
      var out = h('pre', { class: 'Terminal', tabindex: 0 });
      var prompt = h('span', { class: 'prompt' }, '$');
      var inp = h('input', { type: 'text', autocomplete: 'off', autocapitalize: 'off', spellcheck: false, 'aria-label': 'Команда' });
      function put(text, cls) { var s = h('span', { class: cls || '' }, text); out.appendChild(s); out.scrollTop = out.scrollHeight; buf[key] = (buf[key] || []).concat([[text, cls]]).slice(-500); }
      (buf[key] || []).forEach(function (x) { out.appendChild(h('span', { class: x[1] || '' }, x[0])); });
      el.appendChild(h('div', { class: 'Box' },
        h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('terminal'), 'Терминал'), h('span', { class: 'muted', id: 'term-cwd' }),
          h('button', { class: 'btn btn-sm ml-auto', type: 'button', onclick: function () { SCC.clear(out); buf[key] = []; inp.focus(); } }, 'Очистить')),
        out, h('div', { class: 'TerminalInput' }, prompt, inp)));
      function setCwd(c) { cwd = c; var e = SCC.$('#term-cwd'); if (e) e.textContent = c; }
      el.appendChild(h('p', { class: 'muted mt-2' }, 'Команды выполняются по одной (без интерактивного режима) в каталоге, который запоминается через ', h('code', null, 'cd'), '. Время выполнения ограничено 60 с.'));
      api.get('state').then(function (s) { setCwd(s.cwd); prompt.textContent = (s.shell === 'powershell' ? 'PS' : s.user) + '>'; if (!(buf[key] || []).length) put('Подключено. Оболочка: ' + s.shell + '\n', 'meta'); inp.focus(); }, SCC.err);
      var running = false;
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowUp') { e.preventDefault(); if (hi > 0) inp.value = HIST[--hi]; }
        else if (e.key === 'ArrowDown') { e.preventDefault(); hi = Math.min(HIST.length, hi + 1); inp.value = HIST[hi] || ''; }
        else if (e.key === 'Enter') {
          var cmd = inp.value; if (!cmd.trim() || running) return;
          HIST.push(cmd); hi = HIST.length; inp.value = ''; running = true; put(cwd + ' ' + prompt.textContent + ' ' + cmd + '\n', 'cmd');
          api.post('run', { cmd: cmd }).then(function (r) {
            if (r.out) put(r.out.replace(/\n?$/, '\n')); if (r.err) put(r.err.replace(/\n?$/, '\n'), 'err');
            if (r.code) put('[код выхода ' + r.code + ']\n', 'meta'); setCwd(r.cwd);
          }, function (x) { put(x.message + '\n', 'err'); }).then(function () { running = false; inp.focus(); });
        }
      });
      out.addEventListener('click', function () { if (!String(window.getSelection())) inp.focus(); });
    }
  });
})();
