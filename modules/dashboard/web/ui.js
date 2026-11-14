(function () {
  var h = SCC.h, icon = SCC.icon, fmt = SCC.fmt;
  function bar(p) { var c = p >= 90 ? 'crit' : p >= 75 ? 'warn' : ''; return h('div', { class: 'Progress ' + c }, h('span', { style: 'width:' + Math.min(100, Math.max(0, p || 0)) + '%' })); }
  function row(label, p, text) { return h('div', { class: 'stat-row' }, h('div', null, label), bar(p), h('div', { class: 'val' }, text)); }

  SCC.registerPage('dashboard', {
    render: function (el, ctx) {
      var api = SCC.mod('dashboard'), left = h('div'), side = h('div', { class: 'Layout-sidebar' }), timer;
      el.appendChild(h('div', { class: 'Layout', style: 'padding-top:0' }, left, side));
      function draw(s) {
        SCC.clear(left); SCC.clear(side);
        var m = s.memory, cpuTxt = s.cpu == null ? 'измеряется…' : fmt.pct(s.cpu);
        var cpu = h('div', { class: 'Box' }, h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('cpu'), 'Ресурсы')),
          row('Процессор', s.cpu || 0, cpuTxt + ' · ' + s.cpu_count + ' ядер'),
          row('Память', m.total ? m.used / m.total * 100 : 0, fmt.bytes(m.used) + ' / ' + fmt.bytes(m.total)),
          m.swap_total ? row('Подкачка', m.swap_used / m.swap_total * 100, fmt.bytes(m.swap_used) + ' / ' + fmt.bytes(m.swap_total)) : null);
        var dk = h('div', { class: 'Box' }, h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('database'), 'Диски', h('span', { class: 'Counter' }, s.disks.length))),
          s.disks.length ? s.disks.map(function (d) { return row(d.mount, d.total ? d.used / d.total * 100 : 0, fmt.bytes(d.used) + ' / ' + fmt.bytes(d.total)); }) : h('div', { class: 'blankslate' }, 'Нет данных'));
        left.appendChild(cpu); left.appendChild(dk);
        function item(ic, a, b) { return h('div', { class: 'sidebar-item' }, icon(ic), h('span', null, a + ' '), b != null ? h('strong', null, b) : null); }
        side.appendChild(h('div', { class: 'BorderGrid-cell' }, h('h2', null, 'О системе'),
          item('server', 'Имя:', s.hostname), item('code', 'ОС:', s.os), item('cpu', 'Архитектура:', s.arch),
          item('clock', 'Аптайм:', fmt.duration(s.uptime)), s.load ? item('pulse', 'Нагрузка:', s.load.map(function (x) { return x.toFixed(2); }).join(' ')) : null));
        side.appendChild(h('div', { class: 'BorderGrid-cell' }, h('h2', null, 'Панель'),
          item('tag', 'Версия:', s.version), item('package', 'Тип:', s.type === 'panel' ? 'хостинг и нода управления' : 'нода'),
          item('clock', 'Работает:', fmt.duration(s.app_uptime)), item('code', 'Python:', s.python)));
      }
      function tick() {
        return api.get('stats').then(function (s) { if (ctx.isSeq()) draw(s); }).catch(function (e) { if (ctx.isSeq()) { SCC.clear(left).appendChild(SCC.flash('error', e.message)); } });
      }
      timer = setInterval(tick, 3000); ctx.onLeave(function () { clearInterval(timer); });
      return tick();
    }
  });
})();
