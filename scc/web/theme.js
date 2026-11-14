(function () {
  try {
    var t = localStorage.getItem('scc-theme') || 'auto';
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
})();
