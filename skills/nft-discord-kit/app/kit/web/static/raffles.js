// The giveaways page: open giveaways, entered with the Discord login (same rules as the Enter button).
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const say = (text, kind) => {
    $('msg').textContent = text || '';
    $('msg').className = 'msg' + (kind ? ' ' + kind : '');
  };
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const when = (ts) => new Date(ts * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });

  async function api(path, body) {
    const options = { method: body ? 'POST' : 'GET', credentials: 'same-origin', headers: {} };
    if (body) {
      options.headers['content-type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const res = await fetch(path, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Request failed (' + res.status + ')');
    return data;
  }

  function account(user) {
    const box = $('account');
    box.replaceChildren();
    if (!user) {
      const login = el('a', 'btn primary', 'Log in with Discord to enter');
      login.href = '/auth/discord?next=/raffles';
      box.append(login);
      return;
    }
    const out = el('button', 'btn', 'Log out');
    out.type = 'button';
    out.addEventListener('click', async () => {
      await api('/auth/logout', {});
      location.reload();
    });
    box.append(el('span', '', 'Logged in as ' + (user.name || user.id)), out);
  }

  function card(r, user) {
    const li = el('li', 'raffle' + (r.status === 'open' ? '' : ' ended'));
    li.id = r.id;
    const title = el('h3');
    const link = el('a', '', r.title);
    link.href = r.link;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    title.append(link);
    const parts = [r.chain, r.winners + ' winner' + (r.winners === 1 ? '' : 's'), r.entrants + ' entered',
      (r.status === 'open' ? 'ends ' : 'ended ') + when(r.ends)];
    li.append(title, el('div', 'meta', parts.join(' · ')));
    if (r.description) li.append(el('p', '', r.description));
    if (r.status !== 'open' || !user) return li;
    const enter = el('button', 'btn primary', r.entered ? 'Entered: check my tickets' : 'Enter');
    enter.type = 'button';
    const result = el('p', 'msg');
    enter.addEventListener('click', async () => {
      enter.disabled = true;
      try {
        const res = await api('/api/raffles/' + encodeURIComponent(r.id) + '/enter', {});
        const ok = res.code === 'entered' || res.code === 'updated';
        result.textContent = res.message;
        result.className = 'msg ' + (ok ? 'ok' : 'bad');
        if (ok) enter.textContent = 'Entered: check my tickets';
      } catch (e) {
        result.textContent = e.message;
        result.className = 'msg bad';
      }
      enter.disabled = false;
    });
    li.append(enter, result);
    return li;
  }

  async function start() {
    try {
      const data = await api('/api/raffles');
      account(data.user);
      $('list').replaceChildren(...data.raffles.map((r) => card(r, data.user)));
      if (!data.raffles.length) say('No giveaways running right now.');
    } catch (e) {
      say(e.message, 'bad');
    }
  }

  start();
})();
