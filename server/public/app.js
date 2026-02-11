import { marked } from './vendor/marked.esm.js';

marked.setOptions({
  breaks: true,
  gfm: true,
  headerIds: false,
  mangle: false,
});

const state = {
  eventSource: null,
  reconnectDelay: 2000,
  reconnectTimer: null,
  lastId: null,
};

const screenshotEl = document.getElementById('screenshot');
const feedbackEl = document.getElementById('feedback');
const connectionEl = document.getElementById('connection');
const lastUpdateEl = document.getElementById('last-update');
const pingAudio = document.getElementById('ping');

const qrCard = document.getElementById('qr-card');
const qrImage = document.getElementById('qr-image');
const primaryUrlEl = document.getElementById('primary-url');
const urlListEl = document.getElementById('url-list');
let activeAccessUrl = null;

const ALLOWED_TAGS = new Set([
  'p',
  'strong',
  'em',
  'ul',
  'ol',
  'li',
  'code',
  'pre',
  'blockquote',
  'a',
  'br',
  'hr',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
]);

const ALLOWED_ATTRS = {
  a: ['href', 'title', 'target', 'rel'],
  code: [],
  pre: [],
};

const SAFE_URL_PATTERN = /^(https?:|mailto:)/i;

function sanitizeHtml(input) {
  if (!input) return '';
  const doc = new DOMParser().parseFromString(input, 'text/html');
  const elements = Array.from(doc.body.querySelectorAll('*'));

  elements.forEach((el) => {
    const tag = el.tagName.toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) {
      const parent = el.parentNode;
      if (!parent) {
        el.remove();
        return;
      }
      while (el.firstChild) {
        parent.insertBefore(el.firstChild, el);
      }
      parent.removeChild(el);
      return;
    }

    Array.from(el.attributes).forEach((attr) => {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on')) {
        el.removeAttribute(attr.name);
        return;
      }

      const allowed = ALLOWED_ATTRS[tag] || [];
      if (!allowed.includes(name)) {
        el.removeAttribute(attr.name);
        return;
      }

      if ((name === 'href' || name === 'src') && !SAFE_URL_PATTERN.test(attr.value)) {
        el.removeAttribute(attr.name);
        return;
      }

      if (tag === 'a' && name === 'target' && attr.value === '_blank' && !el.hasAttribute('rel')) {
        el.setAttribute('rel', 'noopener noreferrer');
      }
    });
  });

  return doc.body.innerHTML;
}

function renderMarkdownSafe(content) {
  if (!content) return '';
  const rawHtml = marked.parse(content);
  return sanitizeHtml(rawHtml);
}

function setConnection(status, text) {
  connectionEl.classList.remove('chip-success', 'chip-warning', 'chip-error');
  connectionEl.classList.add(`chip-${status}`);
  connectionEl.textContent = text;
}

function renderFeedback(payload, playTone = true) {
  if (!payload) return;

  if (payload.id && payload.id === state.lastId) {
    return;
  }

  state.lastId = payload.id;

  if (payload.screenshotUrl) {
    const cacheBust = `?t=${payload.id || Date.now()}`;
    screenshotEl.src = `${payload.screenshotUrl}${cacheBust}`;
    screenshotEl.alt = `Screenshot @ ${payload.timestamp}`;
    screenshotEl.classList.add('visible');
  }

  feedbackEl.innerHTML = '';
  const content = String(payload.feedback || '').trim();
  if (!content) {
    feedbackEl.textContent = 'Feedback payload was empty.';
  } else {
    const rendered = renderMarkdownSafe(content);
    if (rendered) {
      feedbackEl.innerHTML = rendered;
    } else {
      const paragraph = document.createElement('p');
      paragraph.textContent = content;
      feedbackEl.appendChild(paragraph);
    }
  }

  const timeline = document.createElement('small');
  timeline.className = 'timestamp';
  timeline.textContent = new Date(payload.timestamp || Date.now()).toLocaleString();
  feedbackEl.appendChild(timeline);

  if (payload.meta && Object.keys(payload.meta).length > 0) {
    const details = document.createElement('div');
    details.className = 'meta';

    Object.entries(payload.meta).forEach(([key, value]) => {
      const row = document.createElement('div');
      row.innerHTML = `<span>${key}</span><strong>${value}</strong>`;
      details.appendChild(row);
    });

    feedbackEl.appendChild(details);
  }

  lastUpdateEl.textContent = timeline.textContent;

  if (playTone) {
    pingAudio.currentTime = 0;
    pingAudio.play().catch(() => {});
  }
}

async function fetchLatestFallback() {
  try {
    const res = await fetch('/api/latest');
    if (!res.ok) return;
    const payload = await res.json();
    renderFeedback(payload, false);
  } catch {
    // ignore; SSE will deliver when available
  }
}

function scheduleReconnect() {
  if (state.reconnectTimer) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    connectStream();
  }, state.reconnectDelay);
}

function connectStream() {
  if (state.eventSource) {
    state.eventSource.close();
  }

  setConnection('warning', 'Connecting…');
  state.eventSource = new EventSource('/api/stream');

  state.eventSource.onopen = () => {
    setConnection('success', 'Live');
    state.reconnectDelay = 2000;
  };

  state.eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      renderFeedback(payload, true);
    } catch (error) {
      console.error('Failed to parse payload', error);
    }
  };

  state.eventSource.onerror = () => {
    setConnection('error', 'Reconnecting…');
    state.eventSource.close();
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 15000);
    scheduleReconnect();
  };
}

window.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !state.eventSource) {
    connectStream();
  }
});

fetchLatestFallback();
connectStream();

function setAccessUrl(url) {
  if (!url) return;
  activeAccessUrl = url;

  if (qrImage) {
    qrImage.src = `/api/qr?target=${encodeURIComponent(url)}`;
    qrImage.alt = `QR code for ${url}`;
  }

  if (primaryUrlEl) {
    primaryUrlEl.textContent = url;
    primaryUrlEl.href = url;
  }

  if (urlListEl) {
    urlListEl.querySelectorAll('button').forEach((button) => {
      button.classList.toggle('active', button.dataset.url === url);
    });
  }

  if (qrCard) {
    qrCard.hidden = false;
  }
}

async function hydrateAccessInfo() {
  if (!qrCard || !urlListEl) return;

  try {
    const res = await fetch('/api/info');
    if (!res.ok) throw new Error('info request failed');

    const data = await res.json();
    const urls = Array.isArray(data.urls) ? data.urls : [];

    urlListEl.innerHTML = '';

    if (urls.length === 0) {
      urlListEl.textContent = 'Connect laptop & phone to the same Wi-Fi network.';
      qrCard.hidden = false;
      return;
    }

    urls.forEach((url) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'url-pill';
      button.textContent = url;
      button.dataset.url = url;
      button.addEventListener('click', () => setAccessUrl(url));
      urlListEl.appendChild(button);
    });

    const initial = activeAccessUrl && urls.includes(activeAccessUrl) ? activeAccessUrl : urls[0];
    setAccessUrl(initial);
  } catch (error) {
    console.error('Failed to load LAN URLs', error);
    urlListEl.textContent = 'Unable to detect LAN address automatically.';
    qrCard.hidden = false;
  }
}

hydrateAccessInfo();

