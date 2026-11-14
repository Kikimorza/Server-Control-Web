(function () {
  var h = SCC.h, icon = SCC.icon, fmt = SCC.fmt;
  var TEXT_MAX = 1024 * 1024;

  function goto(path, extra) {
    location.hash = SCC.link('files') + '?path=' + encodeURIComponent(path || '') + (extra || '');
  }
  function join(dir, name, sep) { return dir.replace(/[\\/]+$/, '') + (sep || '/') + name; }

  SCC.registerPage('files', {
    render: function (el, ctx) {
      var api = SCC.mod('files');
      var path = ctx.query.get('path') || '';
      if (ctx.query.get('edit')) return editor(el, ctx, api, path);

      var box = h('div', { class: 'Box' });
      var fileInput = h('input', { type: 'file', multiple: true, class: 'hidden' });
      el.appendChild(box); el.appendChild(fileInput);

      function upload(files, dir, sep) {
        var list = Array.prototype.slice.call(files);
        (function next() {
          var f = list.shift();
          if (!f) { SCC.toast('Загрузка завершена', 'success'); return load(); }
          SCC.toast('Загрузка: ' + f.name);
          api.put('upload', f, { path: dir, name: f.name }).then(next, function (e) { SCC.err(e); load(); });
        })();
      }
      function create(kind, dir, sep) {
        SCC.prompt(kind === 'dir' ? 'Новая папка' : 'Новый файл', 'Имя', { ok: 'Создать' }).then(function (name) {
          if (!name) return;
          api.post('create', { path: dir, name: name, type: kind }).then(function () { if (kind === 'file') goto(join(dir, name, sep), '&edit=1'); else load(); }, SCC.err);
        });
      }
      function load() {
        return api.get('list', { path: path }).then(function (r) {
          if (!ctx.isSeq()) return;
          var sep = r.sep, atRoot = r.path === '';
          SCC.clear(box);
          var crumbs = h('div', { class: 'Crumbs' });
          r.parts.forEach(function (p, i) {
            if (i) crumbs.appendChild(h('span', { class: 'sep' }, '/'));
            var last = i === r.parts.length - 1;
            crumbs.appendChild(last ? h('strong', null, p.name) : h('a', { href: '#', onclick: function (e) { e.preventDefault(); goto(p.path); } }, p.name));
          });
          box.appendChild(h('div', { class: 'Box-header' }, icon('folder'), crumbs,
            atRoot ? null : h('span', { class: 'ml-auto flex gap-2' },
              h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { create('file', r.path, sep); } }, icon('file-plus', 14), 'Файл'),
              h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { create('dir', r.path, sep); } }, icon('folder-plus', 14), 'Папка'),
              h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: function () { fileInput.click(); } }, icon('upload', 14), 'Загрузить'))));
          fileInput.onchange = function () { if (fileInput.files.length) upload(fileInput.files, r.path, sep); fileInput.value = ''; };
          if (r.parent != null && !atRoot) box.appendChild(h('div', { class: 'Box-row hover', style: 'cursor:pointer', onclick: function () { goto(r.parent); } }, h('span', { class: 'icon-col folder' }, icon('folder')), h('a', null, '..')));
          r.entries.forEach(function (e) {
            var full = e.path || join(r.path, e.name, sep), isDir = e.type === 'dir';
            var open = function () {
              if (isDir) goto(full);
              else if (e.size <= TEXT_MAX) goto(full, '&edit=1');
              else window.open(api.url('download', { path: full }));
            };
            box.appendChild(h('div', { class: 'Box-row hover' },
              h('span', { class: 'icon-col' + (isDir ? ' folder' : '') }, icon(isDir ? 'folder' : e.type === 'link' ? 'link' : 'file')),
              h('a', { href: '#', class: 'flex-1 truncate', onclick: function (ev) { ev.preventDefault(); open(); } }, e.name),
              isDir || atRoot ? null : h('span', { class: 'muted num', style: 'width:84px;text-align:right' }, fmt.bytes(e.size)),
              atRoot ? null : h('span', { class: 'muted hide-sm', style: 'width:130px;text-align:right' }, fmt.ago(e.mtime)),
              atRoot ? null : SCC.menu(h('button', { class: 'IconBtn plain', style: 'width:28px;height:28px', type: 'button', 'aria-label': 'Действия' }, icon('kebab')), function () {
                return [!isDir ? { label: 'Скачать', icon: 'download', onclick: function () { window.open(api.url('download', { path: full })); } } : null,
                  !isDir ? { label: 'Редактировать', icon: 'pencil', onclick: function () { goto(full, '&edit=1'); } } : null,
                  { label: 'Переименовать', icon: 'pencil', onclick: function () {
                    SCC.prompt('Переименовать', 'Новое имя', { value: e.name, ok: 'Переименовать' }).then(function (n) { if (n && n !== e.name) api.post('rename', { path: full, name: n }).then(load, SCC.err); });
                  } },
                  { divider: true },
                  { label: 'Удалить', icon: 'trash', danger: true, onclick: function () {
                    SCC.confirm('Удалить «' + e.name + '»?', isDir ? 'Каталог будет удалён вместе со всем содержимым. Это нельзя отменить.' : 'Файл будет удалён. Это нельзя отменить.', { danger: true, ok: 'Удалить' })
                      .then(function (ok) { if (ok) api.post('delete', { path: full }).then(load, SCC.err); });
                  } }];
              }, { })));
          });
          if (!r.entries.length) box.appendChild(h('div', { class: 'blankslate' }, icon('folder', 32), h('p', null, 'Каталог пуст')));
          if (r.truncated) box.appendChild(h('div', { class: 'Box-row muted' }, 'Показаны первые записи каталога - он слишком большой.'));
          // drag&drop
          if (!atRoot) {
            box.ondragover = function (ev) { ev.preventDefault(); box.classList.add('drop-hint'); };
            box.ondragleave = function () { box.classList.remove('drop-hint'); };
            box.ondrop = function (ev) { ev.preventDefault(); box.classList.remove('drop-hint'); if (ev.dataTransfer.files.length) upload(ev.dataTransfer.files, r.path, sep); };
          }
        }, function (e) { if (ctx.isSeq()) SCC.clear(box).appendChild(SCC.flash('error', e.message)); });
      }
      return load();
    }
  });

  function editor(el, ctx, api, path) {
    var ta = h('textarea', { class: 'Editor', spellcheck: false, wrap: 'off' });
    var name = h('strong', null, path), dirty = false;
    var save = h('button', { class: 'btn btn-sm btn-primary', type: 'button', disabled: true }, icon('check', 14), 'Сохранить');
    var back = function () { var d = path.replace(/[\\/][^\\/]*$/, '') || '/'; if (/^[A-Za-z]:$/.test(d)) d += '\\'; goto(d); };
    function doSave() {
      return SCC.busy(save, function () {
        return api.put('write', ta.value, { path: path }, 'text/plain; charset=utf-8').then(function () { dirty = false; save.disabled = true; SCC.toast('Сохранено', 'success'); });
      });
    }
    save.onclick = doSave;
    ta.addEventListener('input', function () { dirty = true; save.disabled = false; });
    ta.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (dirty) doSave(); }
      if (e.key === 'Tab') { e.preventDefault(); var s = ta.selectionStart; ta.setRangeText('    ', s, ta.selectionEnd, 'end'); dirty = true; save.disabled = false; }
    });
    var onUnload = function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', onUnload);
    ctx.onLeave(function () { window.removeEventListener('beforeunload', onUnload); });
    el.appendChild(h('div', { class: 'Box' },
      h('div', { class: 'Box-header' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: function () { if (!dirty || confirm('Есть несохранённые изменения. Выйти?')) back(); } }, icon('arrow-left', 14), 'К списку'), icon('file'), name,
        h('span', { class: 'ml-auto flex gap-2' }, h('a', { class: 'btn btn-sm', href: api.url('download', { path: path }) }, icon('download', 14), 'Скачать'), save)),
      ta));
    return api.get('read', { path: path }).then(function (r) { ta.value = r.content; name.textContent = r.path; ta.focus(); }, function (e) {
      SCC.clear(el).appendChild(SCC.flash('error', e.message, h('button', { class: 'btn btn-sm', type: 'button', onclick: back }, 'К списку')));
    });
  }
})();
