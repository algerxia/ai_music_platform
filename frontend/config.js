// Auto-select API base: Docker nginx (8080) uses same-origin proxy; local static uses API port.
window.STUDIO_CONFIG = Object.freeze({
  API_BASE: (window.location.port === '8080' || window.location.port === '80')
    ? ''
    : 'http://127.0.0.1:8003',
});
