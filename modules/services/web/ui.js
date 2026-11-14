(function () {
  var h = SCC.h, icon = SCC.icon;
  var filterState = { q: '', only: 'all' };

  SCC.registerPage('services', {
    render: function (el, ctx) {
      var api = SCC.mod('services'), all = [], timer;
      var q = h('input', { class: 'form-control', style: 'max-width:320px', placeholder: 'Фильтр по имени или описанию…', value: filterState.q, 'aria-label': 'Фильтр' });
      var sel = h('select', { class: 'form-control', style: 'width:180px' },
        h('option', { value: 'all' }, 'Все службы'), h('option', { value: 'running' }, 'Запущенные'), h('option', { value: 'stopped' }, 'Остановленные'), h('option', { value: 'failed' }, 'С ошибкой'));
      sel.value = filterState.only;
      var box = h('div', { class: 'Box' });
      var admin = SCC.isAdmin();

      function run(name, act) {
        return api.post(encodeURIComponent(name) + '/' + act).then(function () { SCC.toast(name + ': ' + { start: 'запущена', stop: 'остановлена', restart: 'перезапущена', enable: 'автозапуск включён', disable: 'автозапуск выключен' }[act], 'success'); return load(); }, SCC.err);
      }
      function showLogs(s) {
        var pre = h('pre', { class: 'code', style: 'max-height:60vh' }, 'Загрузка…');
        SCC.dialog({ title: 'Журнал: ' + s.name, size: 'xwide', body: pre, actions: [{ label: 'Закрыть' }] });
        api.get(encodeURIComponent(s.name) + '/logs').then(function (r) { pre.textContent = r.log || '(пусто)'; }, function (e) { pre.textContent = e.message; });
      }
      function match(s) {
        var t = (s.name + ' ' + s.display).toLowerCase();
        if (filterState.q && t.indexOf(filterState.q.toLowerCase()) < 0) return false;
        if (filterState.only === 'running') return s.active === 'active';
        if (filterState.only === 'stopped') return s.active !== 'active' && s.active !== 'failed';
        if (filterState.only === 'failed') return s.active === 'failed';
        return true;
      }
      function draw() {
        var rows = all.filter(match), run_ = all.filter(function (s) { return s.active === 'active'; }).length;
        SCC.clear(box).appendChild(h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('container'), 'Службы',
          h('span', { class: 'Counter' }, run_ + ' / ' + all.length)), h('span', { class: 'muted' }, 'запущено / всего')));
        if (!rows.length) { box.appendChild(SCC.blank('container', 'Ничего не найдено', 'Измените фильтр.')); return; }
        var shown = rows.slice(0, 300);
        shown.forEach(function (s) {
          var cls = s.active === 'active' ? 'on' : s.active === 'failed' ? 'bad' : 'off';
          box.appendChild(h('div', { class: 'Box-row hover' },
            h('span', { class: 'status-dot ' + cls, title: s.sub }),
            h('div', { class: 'flex-1', style: 'min-width:0' }, h('div', { class: 'truncate' }, h('strong', null, s.name)), h('div', { class: 'muted truncate', style: 'font-size:12px' }, s.display)),
            h('span', { class: 'Label ' + (s.active === 'active' ? 'Label--success' : s.active === 'failed' ? 'Label--danger' : '') }, s.sub || s.active),
            s.enabled ? h('span', { class: 'muted hide-sm', style: 'width:84px;font-size:12px' }, s.enabled) : null,
            admin ? h('div', { class: 'flex gap-1' },
              s.active === 'active'
                ? [h('button', { class: 'btn btn-sm', type: 'button', title: 'Перезапустить', onclick: function (e) { SCC.busy(e.currentTarget, function () { return run(s.name, 'restart'); }); } }, icon('sync', 14)),
                  h('button', { class: 'btn btn-sm', type: 'button', title: 'Остановить', onclick: function (e) { SCC.busy(e.currentTarget, function () { return run(s.name, 'stop'); }); } }, icon('stop', 14))]
                : h('button', { class: 'btn btn-sm', type: 'button', title: 'Запустить', onclick: function (e) { SCC.busy(e.currentTarget, function () { return run(s.name, 'start'); }); } }, icon('play', 14)),
              SCC.menu(h('button', { class: 'IconBtn', style: 'width:28px;height:28px', type: 'button', 'aria-label': 'Ещё' }, icon('kebab')), function () {
                var on = s.enabled === 'enabled' || s.enabled === 'automatic';
                return [{ label: 'Журнал', icon: 'log', onclick: function () { showLogs(s); } },
                  { label: on ? 'Выключить автозапуск' : 'Включить автозапуск', icon: on ? 'x-circle' : 'check-circle', onclick: function () { run(s.name, on ? 'disable' : 'enable'); } }];
              })) : null));
        });
        if (rows.length > shown.length) box.appendChild(h('div', { class: 'Box-row muted' }, 'Показано ' + shown.length + ' из ' + rows.length + ' - уточните фильтр.'));
      }
      function load() {
        return api.get('list').then(function (r) { if (!ctx.isSeq()) return; all = r.services; draw(); },
          function (e) { if (ctx.isSeq()) SCC.clear(box).appendChild(SCC.flash('error', e.message)); });
      }
      q.addEventListener('input', function () { filterState.q = q.value; draw(); });
      sel.addEventListener('change', function () { filterState.only = sel.value; draw(); });
      el.appendChild(h('div', { class: 'flex gap-2 mb-3 wrap' }, q, sel, h('button', { class: 'btn', type: 'button', onclick: function (e) { SCC.busy(e.currentTarget, load); } }, icon('sync', 14), 'Обновить')));
      el.appendChild(box);
      timer = setInterval(load, 15000); ctx.onLeave(function () { clearInterval(timer); });
      return load();
    }
  });
})();
