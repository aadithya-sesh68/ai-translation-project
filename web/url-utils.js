(() => {
  const pagePath = window.location.pathname;
  const finalSlash = pagePath.lastIndexOf("/");
  const basePath = pagePath.slice(0, Math.max(0, finalSlash)).replace(/\/+$/, "");

  function applicationPath(path) {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    return `${basePath}${normalizedPath}`;
  }

  function websocketUrl(path) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${applicationPath(path)}`;
  }

  window.oraTranslateUrls = Object.freeze({
    applicationPath,
    websocketUrl,
  });
})();
