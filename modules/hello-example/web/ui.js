/* Клиентская часть примера. Глобальный объект SCC даёт всё нужное; DOM можно менять напрямую. */
(function () {
  var h = SCC.h, icon = SCC.icon;
  var api = SCC.mod('hello-example');

  // Страница раздела (в меню она появляется благодаря "nav" в module.json)
  SCC.registerPage('hello', {
    render: function (el, ctx) {
      var out = h('pre', { class: 'code' }, '…');
      var msg = h('input', { class: 'form-control', placeholder: 'Текст приветствия' });
      var accent = h('input', { class: 'form-control mono', placeholder: '#8250df (пусто - по умолчанию)' });
      el.appendChild(h('div', { style: 'max-width:640px' },
        h('div', { class: 'Box mb-3' },
          h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('rocket'), 'Привет, модуль!')),
          h('div', { class: 'Box-body' }, out,
            h('div', { class: 'form-group mt-3' }, h('label', null, 'Приветствие'), msg),
            h('div', { class: 'form-group' }, h('label', null, 'Свой акцентный цвет сайта'), accent),
            h('div', { class: 'flex gap-2' },
              h('button', { class: 'btn btn-primary', type: 'button', onclick: function (e) {
                SCC.busy(e.currentTarget, function () { return api.post('greeting', { greeting: msg.value, accent: accent.value }).then(function () { SCC.toast('Сохранено. Обновите страницу.', 'success'); load(); }); });
              } }, 'Сохранить'),
              h('button', { class: 'btn', type: 'button', onclick: function () {
                api.get('uptime').then(function (r) { SCC.toast(r.out || '-', 'info'); }, SCC.err);
              } }, icon('terminal', 14), 'Команда ОС: uptime')))),
        SCC.flash('info', 'Исходники: modules/hello-example. Описание API модулей: docs/MODULES.md.')));
      function load() { return api.get('hello').then(function (r) { out.textContent = JSON.stringify(r, null, 2); msg.value = r.message; accent.value = r.accent || ''; }); }
      return load();
    }
  });

  // Кнопка в шапке (слот header-right) - показывается на всех страницах
  var btn = h('button', { class: 'IconBtn', type: 'button', title: 'Привет от модуля', onclick: function () {
    api.get('hello').then(function (r) { SCC.toast(r.message + ' (' + r.user + ')', 'success'); }, SCC.err);
  } }, icon('rocket'));
  SCC.mount('header-right', btn);

  // Вкладка в Настройках
  SCC.addSettingsTab({ id: 'hello-example', title: 'Пример модуля', icon: 'rocket', render: function (el) {
    el.appendChild(h('p', null, 'Здесь модуль может показать свои настройки.'));
  } });

  // Пункт в меню «+»
  SCC.menuPlus.push({ label: 'Пример: приветствие', icon: 'rocket', onclick: function () { SCC.go('hello'); } });

  // Подписка на события интерфейса: 'ready' (после входа) и 'route' (смена страницы)
  SCC.on('route', function (r) { document.body.setAttribute('data-page', r.page); });

  // Применить акцентный цвет, заданный в настройках модуля
  api.get('hello').then(function (r) {
    if (r.accent) document.documentElement.style.setProperty('--nav-underline', r.accent);
  }, function () { });
})();
