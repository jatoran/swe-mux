(function () {
  var grid = document.getElementById('plugin-grid');
  var empty = document.getElementById('plugin-empty');
  var count = document.getElementById('plugin-count');
  var refresh = document.getElementById('plugin-refresh');

  function text(tag, className, value) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    return node;
  }

  function list(value) {
    return Array.isArray(value) && value.length ? value.join(', ') : 'none';
  }

  function row(label, value) {
    var item = document.createElement('div');
    item.className = 'plugin-meta-row';
    item.appendChild(text('dt', '', label));
    item.appendChild(text('dd', '', value));
    return item;
  }

  function card(item) {
    var manifest = item.manifest || {};
    var repository = item.repository || {};
    var article = document.createElement('article');
    article.className = 'plugin-public-card';

    var head = document.createElement('header');
    var title = document.createElement('div');
    title.appendChild(text('span', 'plugin-badge ' + (item.official ? 'official' : ''), item.official ? 'official' : 'community'));
    title.appendChild(text('h3', '', manifest.name || repository.full_name || 'Unnamed plugin'));
    title.appendChild(text('code', '', manifest.id || 'missing id'));
    head.appendChild(title);
    head.appendChild(text('span', 'plugin-version', manifest.version ? 'v' + manifest.version : 'unversioned'));
    article.appendChild(head);
    article.appendChild(text('p', 'plugin-description', manifest.description || repository.description || 'No description supplied.'));

    var details = document.createElement('dl');
    details.className = 'plugin-public-meta';
    details.appendChild(row('Platforms', list(manifest.platforms)));
    details.appendChild(row('Runtime', list(manifest.runtime_requirements)));
    details.appendChild(row('Permissions', list(manifest.permissions)));
    details.appendChild(row('License', manifest.license || repository.license || 'not declared'));
    details.appendChild(row('Revision', String(item.indexed_ref || '').slice(0, 12) || 'unavailable'));
    article.appendChild(details);

    var install = document.createElement('div');
    install.className = 'plugin-install';
    var command = 'swemux plugin install ' + repository.full_name;
    if (item.install_ref) command += ' --ref ' + item.install_ref;
    install.appendChild(text('code', '', command));
    var source = document.createElement('a');
    source.href = repository.url;
    source.textContent = 'source';
    source.rel = 'noreferrer';
    install.appendChild(source);
    if (item.release_url) {
      var release = document.createElement('a');
      release.href = item.release_url;
      release.textContent = 'release';
      release.rel = 'noreferrer';
      install.appendChild(release);
    }
    article.appendChild(install);
    return article;
  }

  function fail(message) {
    grid.replaceChildren();
    empty.textContent = message;
    empty.hidden = false;
    count.textContent = 'unavailable';
  }

  function render(catalog) {
    if (catalog.schema !== 1 || !Array.isArray(catalog.plugins)) throw new Error('unsupported catalog');
    grid.replaceChildren();
    catalog.plugins.forEach(function (plugin) { grid.appendChild(card(plugin)); });
    count.textContent = catalog.plugins.length + (catalog.plugins.length === 1 ? ' plugin' : ' plugins');
    if (!catalog.plugins.length) fail('No validated plugins are published yet.');
  }

  function load() {
    refresh.disabled = true;
    count.textContent = 'loading';
    empty.hidden = true;
    fetch('catalog.json', { cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .then(function (catalog) {
        render(catalog);
      })
      .catch(function () {
        fail('The catalog is unavailable. Direct installation by GitHub owner/repository still works in swe-mux.');
      })
      .finally(function () { refresh.disabled = false; });
  }

  refresh.addEventListener('click', load);
  window.addEventListener('swemux:plugin-catalog', function (event) {
    render(event.detail);
  });
  if (window.SWEMUX_PLUGIN_CATALOG) {
    try { render(window.SWEMUX_PLUGIN_CATALOG); }
    catch (error) { fail('The bundled catalog is invalid.'); }
    refresh.disabled = false;
  } else {
    load();
  }
})();
