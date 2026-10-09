(() => {
  const $ = (s) => document.querySelector(s);
  const log = $('#log'), input = $('#input'), send = $('#send'), hero = $('#hero'), scroll = $('#scroll');
  let sid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)) + '';
  let busy = false;

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function md(src) {
    let s = esc(src);
    s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, c) => '<pre><code>' + c.replace(/\n$/, '') + '</code></pre>');
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
  function setBusy(v) { busy = v; send.disabled = v || !input.value.trim(); }

  async function ask(text) {
    if (busy || !text.trim()) return;
    hero.classList.add('hide');
    add('user', esc(text));
    input.value = ''; input.style.height = 'auto';
    setBusy(true);
    const bub = add('ai', '<span class="dots"><span></span><span></span><span></span></span>');
    let acc = '';
    try {
      const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: sid, message: text})});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        bub.parentElement.classList.add('err'); bub.textContent = j.error || 'Something went wrong.'; setBusy(false); return;
      }
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
            if (j.err) { bub.parentElement.classList.add('err'); bub.textContent = j.err; }
          } catch (_) {}
        }
      }
    } catch (e) {
      bub.parentElement.classList.add('err'); bub.textContent = 'Connection problem. Please try again.';
    }
    setBusy(false); input.focus();
  }

  $('#form').addEventListener('submit', (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input.value); } });
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 180) + 'px'; send.disabled = busy || !input.value.trim(); });
  document.querySelectorAll('.chip').forEach((c) => c.addEventListener('click', () => { input.value = c.textContent + ': '; input.focus(); input.dispatchEvent(new Event('input')); }));
  $('#new').addEventListener('click', () => {
    fetch('/api/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: sid})}).catch(() => {});
    sid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)) + '';
    log.innerHTML = ''; hero.classList.remove('hide'); input.focus();
  });
  send.disabled = true; input.focus();
})();
