(() => {
  const $ = (s) => document.querySelector(s);
  const log = $('#log'), input = $('#input'), send = $('#send'), hero = $('#hero'), scroll = $('#scroll');
  let sid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)) + '';
  let busy = false;

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function md(src) {
    let s = esc(src);
    s = s.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_, l, c) => (l === 'web-search' ? '<div class="lbl">Searching the web</div>' : l === 'python-run' ? '<div class="lbl">Code for the computer</div>' : '') + '<pre><code>' + c.replace(/\n$/, '') + '</code></pre>');
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?]|$)/g, '$1<em>$2</em>');
    return s;
  }
  function add(role, html, cls) {
    const row = document.createElement('div');
    row.className = 'msg ' + role + (cls ? ' ' + cls : '');
    if (role === 'ai') {
      const img = document.createElement('img');
      img.src = '/static/crayon.svg'; img.className = 'av'; img.alt = '';
      row.appendChild(img);
    }
    const b = document.createElement('div'); b.className = 'bubble'; b.innerHTML = html;
    row.appendChild(b); log.appendChild(row);
    scroll.scrollTop = scroll.scrollHeight;
    return b;
  }
  function setBusy(v) { busy = v; send.disabled = v || (!input.value.trim() && !pending.length); }

  // ---- virtual computer (Python in the visitor's browser) ----
  const store = new Map();            // name -> ArrayBuffer (attached + generated files)
  let pending = [];                   // attached, not yet sent
  let worker = null, wid = 0;
  const filesBox = $('#files'), fileInput = $('#fileinput');
  const MAX_FILE = 5 * 1024 * 1024, MAX_TOTAL = 20 * 1024 * 1024, MAX_FILES = 6;
  const safeName = (n) => (n.split(/[\\/]/).pop() || 'file').replace(/[^\w.\- ]+/g, '_').slice(0, 80) || 'file';
  const kb = (n) => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
  const total = () => [...store.values()].reduce((a, b) => a + b.byteLength, 0);

  function renderPending() {
    filesBox.innerHTML = ''; filesBox.classList.toggle('hide', !pending.length);
    pending.forEach((n) => {
      const c = document.createElement('span'); c.className = 'fchip';
      c.append(n + ' (' + kb(store.get(n).byteLength) + ') ');
      const x = document.createElement('button'); x.type = 'button'; x.textContent = '\u00d7'; x.setAttribute('aria-label', 'Remove ' + n);
      x.onclick = () => { pending = pending.filter((p) => p !== n); store.delete(n); renderPending(); };
      c.appendChild(x); filesBox.appendChild(c);
    });
    send.disabled = busy || (!input.value.trim() && !pending.length);
  }
  $('#attach').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    for (const f of fileInput.files) {
      const n = safeName(f.name);
      if (store.size >= MAX_FILES) { toast('Max ' + MAX_FILES + ' files per chat.'); break; }
      if (f.size > MAX_FILE) { toast(n + ' is over 5 MB.'); continue; }
      if (total() + f.size > MAX_TOTAL) { toast('Total file limit (20 MB) reached.'); break; }
      store.set(n, await f.arrayBuffer());
      if (!pending.includes(n)) pending.push(n);
    }
    fileInput.value = ''; renderPending();
  });
  function toast(m) { const b = add('ai', esc(m), 'err'); setTimeout(() => b.parentElement.remove(), 4000); }

  function killWorker() { if (worker) { worker.terminate(); worker = null; } }
  function runPython(code, card) {
    return new Promise((resolve) => {
      if (!worker) worker = new Worker('/static/pyworker.js');
      const id = ++wid; let timer;
      const arm = (ms) => { clearTimeout(timer); timer = setTimeout(() => { killWorker(); resolve({stdout: '', error: 'Timed out (the computer is limited to 30 seconds per run).', files: []}); }, ms); };
      arm(120000);
      const files = {}; store.forEach((v, k) => { files[k] = v.slice(0); });
      worker.onmessage = (e) => {
        if (e.data.id !== id) return;
        if (e.data.status) { card.querySelector('.st').textContent = e.data.status; if (e.data.status === 'Running...') arm(30000); return; }
        clearTimeout(timer); resolve(e.data);
      };
      worker.onerror = () => { clearTimeout(timer); killWorker(); resolve({stdout: '', error: 'The computer crashed.', files: []}); };
      worker.postMessage({id, code, files});
    });
  }
  function runCard() {
    const row = document.createElement('div'); row.className = 'msg ai';
    const img = document.createElement('img'); img.src = '/static/crayon.svg'; img.className = 'av'; img.alt = '';
    const box = document.createElement('div'); box.className = 'run busy';
    box.innerHTML = '<div class="hd"><i></i><span>Crayon\'s computer</span><span class="st">Starting...</span></div><pre class="out hide"></pre><div class="outfiles"></div>';
    row.append(img, box); log.appendChild(row); scroll.scrollTop = scroll.scrollHeight; return box;
  }
  function finishCard(card, res) {
    card.classList.remove('busy'); card.querySelector('.st').textContent = res.error ? 'Finished with an error' : 'Done';
    const o = card.querySelector('.out'), txt = ((res.stdout || '') + (res.error ? '\n' + res.error : '')).trim();
    if (txt) { o.textContent = txt.slice(0, 6000); o.classList.remove('hide'); }
    const ofs = card.querySelector('.outfiles');
    (res.files || []).forEach((f) => {
      const n = safeName(f.name); store.set(n, f.data);
      const blob = new Blob([f.data], {type: /\.(png|jpe?g|gif|webp)$/i.test(n) ? 'image/' + n.split('.').pop().toLowerCase().replace('jpg', 'jpeg') : 'application/octet-stream'});
      const url = URL.createObjectURL(blob);
      if (/\.(png|jpe?g|gif|webp)$/i.test(n)) { const im = document.createElement('img'); im.src = url; im.alt = n; ofs.appendChild(im); }
      const a = document.createElement('a'); a.href = url; a.download = n; a.textContent = 'Download ' + n; ofs.appendChild(a);
    });
    scroll.scrollTop = scroll.scrollHeight;
  }
  function searchCard(q) {
    const row = document.createElement('div'); row.className = 'msg ai';
    const img = document.createElement('img'); img.src = '/static/crayon.svg'; img.className = 'av'; img.alt = '';
    const box = document.createElement('div'); box.className = 'run busy';
    box.innerHTML = '<div class="hd"><i></i><span>Searching the web</span><span class="st"></span></div><div class="outfiles srcs"></div>';
    box.querySelector('.st').textContent = q;
    row.append(img, box); log.appendChild(row); scroll.scrollTop = scroll.scrollHeight; return box;
  }
  function finishSearch(card, items, err) {
    card.classList.remove('busy');
    const box = card.querySelector('.srcs');
    if (!items.length) { box.textContent = err || 'No results.'; return; }
    items.slice(0, 6).forEach((x) => {
      let host = ''; try { const u = new URL(x.url); if (!/^https?:$/.test(u.protocol)) return; host = u.hostname.replace(/^www\./, ''); } catch (_) { return; }
      const a = document.createElement('a'); a.href = x.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = host; a.title = x.title; box.appendChild(a);
    });
    scroll.scrollTop = scroll.scrollHeight;
  }
  async function previews(names) {
    let out = '';
    for (const n of names) {
      const buf = store.get(n);
      let t = '';
      if (/\.(txt|csv|tsv|json|md|py|js|html|xml|yml|yaml|log|sql)$/i.test(n)) { try { t = new TextDecoder().decode(buf.slice(0, 1200)); } catch (_) {} }
      out += '- ' + n + ' (' + kb(buf.byteLength) + ')' + (t ? '\n  start of file:\n' + t.split('\n').map((l) => '  | ' + l).join('\n') : '') + '\n';
    }
    return out;
  }

  async function stream(text, bub) {
    let acc = '';
    try {
      const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: sid, message: text})});
      if (!r.ok) { const j = await r.json().catch(() => ({})); bub.parentElement.classList.add('err'); bub.textContent = j.error || 'Something went wrong.'; return null; }
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) {
        const {value, done} = await rd.read(); if (done) break;
        buf += dec.decode(value, {stream: true});
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
          if (!line.startsWith('data:')) continue;
          const d = line.slice(5).trim(); if (d === '[DONE]') continue;
          try {
            const j = JSON.parse(d);
            if (j.t) { acc += j.t; bub.innerHTML = md(acc); scroll.scrollTop = scroll.scrollHeight; }
            if (j.err) { bub.parentElement.classList.add('err'); bub.textContent = j.err; return null; }
          } catch (_) {}
        }
      }
    } catch (e) { bub.parentElement.classList.add('err'); bub.textContent = 'Connection problem. Please try again.'; return null; }
    return acc;
  }

  async function ask(raw) {
    const typed = raw.trim();
    if (busy || (!typed && !pending.length)) return;
    hero.classList.add('hide');
    const names = pending.slice(); pending = []; renderPending();
    let text = typed || 'Here are my files.';
    let shown = esc(text) + (names.length ? '<div class="lbl" style="color:#ddd">' + names.map(esc).join(', ') + '</div>' : '');
    if (names.length) {
      let p = await previews(names);
      text += '\n\n[Attached files, available in /work]\n' + p;
      if (text.length > 8800) text = text.slice(0, 8800) + '\n[truncated]';
    }
    add('user', shown);
    input.value = ''; input.style.height = 'auto';
    setBusy(true);
    let msg = text;
    for (let step = 0; step < 6; step++) {
      const bub = add('ai', '<span class="dots"><span></span><span></span><span></span></span>');
      const acc = await stream(msg, bub);
      if (acc === null) break;
      const sm = acc.match(/```web-search\n([\s\S]*?)```/);
      if (sm && step < 5) {
        const rest = acc.replace(sm[0], '').trim(); if (rest) bub.innerHTML = md(rest); else bub.parentElement.remove();
        const q = sm[1].trim().split('\n')[0].slice(0, 200);
        const sc = searchCard(q);
        let items = [], err = '';
        try {
          const r = await fetch('/api/search', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({q})});
          const jj = await r.json().catch(() => ({}));
          if (r.ok) items = jj.results || []; else err = jj.error || 'Search failed.';
        } catch (_) { err = 'Search failed.'; }
        finishSearch(sc, items, err);
        msg = '[Search results for: ' + q + ']\n' + (items.length ? items.map((x, i) => (i + 1) + '. ' + x.title + (x.date ? ' (' + x.date + ')' : '') + '\n   ' + x.url + '\n   ' + x.snippet).join('\n') : '(no results' + (err ? ': ' + err : '') + ')');
        continue;
      }
      const m = acc.match(/```python-run\n([\s\S]*?)```/);
      if (!m || step === 5) break;
      const card = runCard();
      const res = await runPython(m[1], card);
      finishCard(card, res);
      const fl = (res.files || []).map((f) => f.name).join(', ');
      msg = '[Computer output]\n' + ((res.stdout || '').slice(0, 2500) || '(no output)') + (res.error ? '\n[Error]\n' + res.error.slice(0, 1200) : '') + (fl ? '\n[Files now in /work: ' + fl + ']' : '');
    }
    setBusy(false); input.focus();
  }

  $('#form').addEventListener('submit', (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input.value); } });
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 180) + 'px'; send.disabled = busy || (!input.value.trim() && !pending.length); });
  document.querySelectorAll('.chip').forEach((c) => c.addEventListener('click', () => { input.value = c.textContent + ': '; input.focus(); input.dispatchEvent(new Event('input')); }));
  $('#new').addEventListener('click', () => {
    fetch('/api/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: sid})}).catch(() => {});
    sid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)) + '';
    killWorker(); store.clear(); pending = []; renderPending(); log.innerHTML = ''; hero.classList.remove('hide'); input.focus();
  });
  send.disabled = true; input.focus();
})();
