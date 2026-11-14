(function () {
  var h = SCC.h, icon = SCC.icon, fmt = SCC.fmt;

  SCC.registerPage('nodes', {
    render: function (el, ctx) {
      var api = SCC.mod('nodes', true); // список нод хранится на панели - всегда локальные запросы
      var list = h('div', { class: 'Box' }), timer, last = null;
      var boot = SCC.state.boot;

      function openNode(id) { location.hash = '#/n/' + encodeURIComponent(id) + '/'; }

      function dialog(node) {
        var edit = !!node;
        var name = h('input', { class: 'form-control', value: edit ? node.name : '', placeholder: 'Например: Хостинг-2' });
        var url = h('input', { class: 'form-control mono', value: edit ? node.url : '', placeholder: 'https://192.168.1.20:8080' });
        var tok = h('input', { class: 'form-control mono', type: 'password', autocomplete: 'off', placeholder: edit ? 'оставьте пустым, чтобы не менять' : 'scn_…' });
        var ins = h('input', { type: 'checkbox', id: 'n-ins', checked: edit && node.insecure });
        var err = h('div', { class: 'errmsg hidden' });
        function f(label, inp, hint) { return h('div', { class: 'form-group' }, h('label', null, label), inp, hint ? h('div', { class: 'form-hint' }, hint) : null); }
        SCC.dialog({
          title: edit ? 'Изменить ноду' : 'Добавить ноду', size: 'wide',
          body: h('div', null,
            SCC.flash('info', 'Токен создаётся на самой ноде: Настройки → Доступ ноды (или показывается в конце первичной настройки).'),
            f('Название', name), f('Адрес', url, 'Адрес веб-интерфейса ноды, включая порт.'), f('Токен доступа', tok),
            h('div', { class: 'form-check' }, ins, h('div', null, h('label', { for: 'n-ins' }, 'Не проверять TLS-сертификат'), h('div', { class: 'form-hint' }, 'Для самоподписанных сертификатов. Менее безопасно.'))), err),
          actions: [{ label: 'Отмена' }, { label: edit ? 'Сохранить' : 'Подключить', kind: 'primary', onclick: function (close, btn) {
            err.classList.add('hidden');
            var body = { name: name.value, url: url.value, token: tok.value, insecure: ins.checked };
            SCC.busy(btn, function () {
              return (edit ? api.post(node.id + '/update', body) : api.post('add', body)).then(function () {
                close(); SCC.toast(edit ? 'Сохранено' : 'Нода подключена', 'success');
                return refreshBoot().then(load);
              }, function (e) { err.textContent = e.message; err.classList.remove('hidden'); });
            });
          } }]
        });
      }

      function refreshBoot() { // обновить список в выпадающем меню шапки
        return SCC.api('/api/boot', { quiet: true }).then(function (b) { SCC.state.nodes = b.nodes || []; });
      }

      function dot(on) { return h('span', { class: 'status-dot ' + (on ? 'on' : 'off'), title: on ? 'в сети' : 'не отвечает' }); }

      function draw(data) {
        SCC.clear(list);
        list.appendChild(h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('globe'), 'Ноды', h('span', { class: 'Counter' }, data.length + 1)),
          h('button', { class: 'btn btn-sm btn-primary ml-auto', type: 'button', onclick: function () { dialog(); } }, icon('plus', 14), 'Добавить ноду')));
        list.appendChild(h('div', { class: 'Box-row hover' }, dot(true), icon('server'),
          h('div', { class: 'flex-1' }, h('strong', null, boot.name || boot.hostname), ' ', h('span', { class: 'muted' }, 'эта панель')),
          h('span', { class: 'Label' }, boot.type_label), h('span', { class: 'Counter' }, boot.version)));
        data.forEach(function (n) {
          var info = n.info || {};
          list.appendChild(h('div', { class: 'Box-row hover' }, dot(n.online), icon('server'),
            h('div', { class: 'flex-1', style: 'min-width:0' },
              h('div', null, h('a', { href: '#/n/' + encodeURIComponent(n.id) + '/', onclick: function (e) { e.preventDefault(); openNode(n.id); } }, n.name),
                n.online ? [' ', h('span', { class: 'Label' }, info.type === 'panel' ? 'хостинг и нода управления' : 'нода')] : null),
              h('div', { class: 'muted truncate', style: 'font-size:12px' }, n.url + (n.online ? ' · ' + (info.os || '') : ' · ' + (n.error || 'нет связи')))),
            n.online ? [h('span', { class: 'muted' }, n.ms + ' мс'), h('span', { class: 'Counter' }, info.version)] : null,
            h('button', { class: 'btn btn-sm', type: 'button', disabled: !n.online, onclick: function () { openNode(n.id); } }, 'Открыть'),
            SCC.menu(h('button', { class: 'IconBtn', type: 'button', 'aria-label': 'Действия' }, icon('kebab')), function () {
              return [{ label: 'Изменить', icon: 'pencil', onclick: function () { dialog(n); } },
                { label: 'Удалить', icon: 'trash', danger: true, onclick: function () {
                  SCC.confirm('Удалить ноду «' + n.name + '»?', 'Нода будет убрана из панели. Сама нода продолжит работать.', { danger: true, ok: 'Удалить' }).then(function (ok) {
                    if (ok) api.del(n.id).then(function () { return refreshBoot(); }).then(load, SCC.err);
                  });
                } }];
            })));
        });
        if (!data.length) list.appendChild(h('div', { class: 'blankslate' }, icon('globe', 32), h('h3', null, 'Нод пока нет'),
          h('p', null, 'Установите Server Control Web на другой сервер с типом «Нода», скопируйте токен и добавьте её сюда.'),
          h('button', { class: 'btn btn-primary', type: 'button', onclick: function () { dialog(); } }, icon('plus', 14), 'Добавить ноду')));
      }

      function load() {
        return api.get('status').then(function (r) { if (ctx.isSeq()) { last = r.nodes; draw(last); } }, function (e) { if (ctx.isSeq()) SCC.clear(list).appendChild(SCC.flash('error', e.message)); });
      }

      el.appendChild(h('div', { style: 'max-width:960px' }, list));
      el.appendChild(h('p', { class: 'muted mt-3', style: 'max-width:960px' },
        'Выберите ноду в верхнем меню (рядом с названием) или нажмите «Открыть»: терминал, файлы, службы и остальные модули будут работать на этой ноде. Доступно только администраторам.'));
      draw([]);
      timer = setInterval(load, 10000); ctx.onLeave(function () { clearInterval(timer); });
      if (ctx.args[0] === 'add') setTimeout(function () { dialog(); }, 60);
      return load();
    }
  });
})();
