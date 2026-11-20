// This module bridges the existing Yew page to the independent egui WebRunner.
const canvasId = 'lantern-cash-forecast-canvas';
const statusId = 'lantern-forecast-status';
let modulePromise;
let operation = Promise.resolve();
let active;

function setStatus(message, canvas) {
  if (active?.canvas !== canvas || !canvas.isConnected) return;
  const status = document.getElementById(statusId);
  if (status) status.textContent = message;
}

async function loadModule() {
  if (!modulePromise) {
    modulePromise = import('./forecast/forecast_plot.js').then(async (module) => {
      await module.default();
      return module;
    }).catch((error) => {
      modulePromise = undefined;
      throw error;
    });
  }
  return modulePromise;
}

function enqueue(action) {
  operation = operation.then(action, action);
  return operation;
}

function unmount(id = canvasId) {
  const record = active;
  if (!record || record.canvas.id !== id) return Promise.resolve();
  active = undefined;
  record.cancelled = true;
  record.events.abort();
  return enqueue(() => {
    if (record.started) {
      record.module.stop();
      record.started = false;
    }
  });
}

function mount(id = canvasId) {
  const canvas = document.getElementById(id);
  if (!(canvas instanceof HTMLCanvasElement)) return Promise.resolve(false);
  if (active?.canvas === canvas) return active.ready;
  if (active) unmount(active.canvas.id);

  const record = {canvas, cancelled: false, started: false, events: new AbortController()};
  active = record;
  // No document-level keyboard handlers: browser zoom remains available outside
  // the focused chart. The egui canvas owns its own interactions.
  if (!canvas.hasAttribute('tabindex')) canvas.tabIndex = 0;
  canvas.addEventListener('pointerdown', () => canvas.focus({preventScroll: true}), {signal: record.events.signal});
  setStatus('Loading interactive financial charts…', canvas);
  record.ready = enqueue(async () => {
    try {
      const module = await loadModule();
      if (record.cancelled || !canvas.isConnected) return false;
      record.module = module;
      await module.start(canvas);
      record.started = true;
      if (record.cancelled || !canvas.isConnected) {
        module.stop();
        record.started = false;
        return false;
      }
      setStatus('Financial charts ready. Switch between the illustrative cash forecast and synthetic expense mix using the chart controls.', canvas);
      return true;
    } catch (error) {
      if (record.module) {
        try { record.module.stop(); } catch (cleanupError) {
          console.warn('Lantern forecast cleanup failed:', cleanupError);
        }
        record.started = false;
      }
      setStatus('The financial charts could not load. Rebuild the chart assets and check that WebGL is available.', canvas);
      console.error('Lantern forecast failed to start:', error);
      return false;
    }
  });
  return record.ready;
}

function syncCanvas() {
  if (active && !active.canvas.isConnected) unmount(active.canvas.id);
  if (document.getElementById(canvasId)) mount();
}

window.LanternForecast = {mount, unmount};
// Yew can add/remove the canvas after this module loads. Mount regardless of its
// viewport position so an initially below-the-fold chart is ready on scroll.
new MutationObserver(syncCanvas).observe(document.documentElement, {childList: true, subtree: true});
syncCanvas();
window.addEventListener('pagehide', () => unmount());
window.addEventListener('pageshow', syncCanvas);
