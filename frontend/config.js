// Same-origin when behind nginx / public tunnel; only local static/vite hits :8003.
const port = window.location.port;
const localStatic = ['5173', '3000', '5500'].includes(port);
window.STUDIO_CONFIG = Object.freeze({
  API_BASE: localStatic ? 'http://127.0.0.1:8003' : '',
});
