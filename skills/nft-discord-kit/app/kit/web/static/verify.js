// The verify page: connect a wallet, sign one free message, link it to the Discord account.
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const flags = document.body.dataset;
  const STATE = new URLSearchParams(location.search).get('state') || '';
  const UA = navigator.userAgent;
  const MOBILE = /Android|iPhone|iPad|iPod/i.test(UA);
  // Already inside a wallet's own browser: never offer "open in wallet" links there.
  const IN_WALLET = /MetaMask|Trust|CoinbaseWallet|CoinbaseBrowser|Phantom|Solflare|Backpack|Rainbow|Zerion|OKApp|Bitget|imToken|TokenPocket/i.test(UA);
  // Discord, X, Instagram and friends open links in their own window, where no wallet exists.
  const IN_APP = /Discord|FBAN|FBAV|Instagram|Twitter|Line\//i.test(UA);
  const announced = [];
  let pending = null;

  const say = (text, kind) => {
    $('msg').textContent = text || '';
    $('msg').className = 'msg' + (kind ? ' ' + kind : '');
  };
  const short = (a) => (a.length > 14 ? a.slice(0, 6) + '...' + a.slice(-4) : a);
  const timeout = (promise, ms, text) =>
    Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error(text)), ms))]);
  const refused = (e) => e && (e.code === 4001 || /reject|denied|cancel/i.test(e.message || ''));
  const fail = (e) => say(refused(e) ? 'Nothing was signed and nothing was linked. Try again when you are ready.'
                                     : (e && e.message) || String(e), 'bad');

  async function api(path, body) {
    const options = { method: body ? 'POST' : 'GET', credentials: 'same-origin', headers: {} };
    if (body) {
      options.headers['content-type'] = 'application/json';
      options.body = JSON.stringify(STATE ? { ...body, state: STATE } : body);
    }
    const res = await fetch(path, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Request failed (' + res.status + ')');
    return data;
  }

  // EVM wallets announce themselves (EIP-6963); older ones only set window.ethereum.
  window.addEventListener('eip6963:announceProvider', (event) => {
    const d = event.detail;
    if (d && d.provider && !announced.some((x) => x.provider === d.provider)) {
      announced.push(d);
      if (!$('connect').hidden) render();
    }
  });
  window.dispatchEvent(new Event('eip6963:requestProvider'));

  function evmChoices() {
    if (announced.length) return announced.map((d) => ({ name: d.info.name, icon: d.info.icon, provider: d.provider }));
    const eth = window.ethereum;
    if (!eth) return [];
    const list = eth.providers && eth.providers.length ? eth.providers : [eth];
    return list.map((p) => ({
      name: p.isMetaMask ? 'MetaMask' : p.isCoinbaseWallet ? 'Coinbase Wallet' : p.isRabby ? 'Rabby' : 'Browser wallet',
      provider: p,
    }));
  }

  function solChoices() {
    if (flags.solana !== '1') return [];
    const out = [];
    if (window.phantom && window.phantom.solana) out.push({ name: 'Phantom', provider: window.phantom.solana });
    if (window.solflare && window.solflare.isSolflare) out.push({ name: 'Solflare', provider: window.solflare });
    if (window.backpack) out.push({ name: 'Backpack', provider: window.backpack.solana || window.backpack });
    if (!out.length && window.solana) out.push({ name: 'Solana wallet', provider: window.solana });
    return out;
  }

  const note = (text) => {
    const p = document.createElement('p');
    p.textContent = text;
    return p;
  };

  function button(label, icon, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn';
    if (icon && /^data:image\//.test(icon)) {
      const img = document.createElement('img');
      img.src = icon;
      img.alt = '';
      b.append(img);
    }
    b.append(document.createTextNode(label));
    b.addEventListener('click', onClick);
    return b;
  }

  function render() {
    const evm = evmChoices();
    const sol = solChoices();
    $('evm-list').replaceChildren(...(evm.length
      ? evm.map((w) => button(w.name, w.icon, () => connectEvm(w.provider)))
      : [note('No EVM wallet found in this browser.')]));
    $('sol').hidden = flags.solana !== '1';
    $('sol-list').replaceChildren(...(sol.length
      ? sol.map((w) => button(w.name, null, () => connectSol(w.provider)))
      : [note('No Solana wallet found in this browser.')]));
  }

  async function connectEvm(provider) {
    try {
      say('Opening your wallet...');
      // Ask quietly first: inside a wallet app the account is usually shared already, and a second
      // eth_requestAccounts popup on top of the first can freeze some mobile wallets.
      let accounts = await Promise.resolve(provider.request({ method: 'eth_accounts' })).catch(() => []);
      if (!accounts || !accounts[0]) {
        say('Approve the connection in your wallet.');
        accounts = await timeout(provider.request({ method: 'eth_requestAccounts' }), 60000,
          'The wallet did not answer. Open it, approve the connection, then try again.');
      }
      const address = String((accounts && accounts[0]) || '').toLowerCase();
      if (!address) throw new Error('The wallet did not share an account.');
      ready({ kind: 'evm', address, provider });
    } catch (e) {
      fail(e);
    }
  }

  async function connectSol(provider) {
    try {
      say('Opening your wallet...');
      const res = await timeout(Promise.resolve(provider.connect()), 60000,
        'The wallet did not answer. Open it, approve the connection, then try again.');
      const key = (res && res.publicKey) || provider.publicKey;
      if (!key) throw new Error('The wallet did not share an account.');
      ready({ kind: 'solana', address: key.toString(), provider });
    } catch (e) {
      fail(e);
    }
  }

  // Signing is its own tap: phone wallets open the signing sheet only right after a user gesture.
  function ready(wallet) {
    pending = wallet;
    $('sign').textContent = 'Sign to link ' + short(wallet.address);
    $('sign').hidden = false;
    $('sign').disabled = false;
    say('Wallet connected. Now press Sign: it is a free message, not a transaction.');
  }

  const toHex = (text) => '0x' + Array.from(new TextEncoder().encode(text), (b) => b.toString(16).padStart(2, '0')).join('');
  const toBase64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));

  async function sign(wallet, message) {
    if (wallet.kind === 'evm') {
      return wallet.provider.request({ method: 'personal_sign', params: [toHex(message), wallet.address] });
    }
    const res = await wallet.provider.signMessage(new TextEncoder().encode(message), 'utf8');
    return toBase64(res && res.signature ? res.signature : res);
  }

  $('sign').addEventListener('click', async () => {
    if (!pending) return;
    const wallet = pending;
    $('sign').disabled = true;
    try {
      const challenge = await api('/api/nonce', { kind: wallet.kind, address: wallet.address });
      say('Sign the message in your wallet.');
      const signature = await timeout(sign(wallet, challenge.message), 120000,
        'No signature after 2 minutes. Open your wallet and press Sign.');
      say('Signature received. Checking your NFTs...');
      done(await api('/api/link', { nonce: challenge.nonce, signature }));
    } catch (e) {
      $('sign').disabled = false;
      fail(e);
    }
  });

  function done(res) {
    pending = null;
    $('connect').hidden = true;
    $('mobile').hidden = true;
    $('sign').hidden = true;
    $('title').textContent = res.member ? 'You are verified' : 'Wallet linked';
    $('lead').textContent = res.summary + '.' + (res.moved
      ? ' This wallet was linked to another Discord account before; it now belongs to this one.' : '');
    say('Done. You can close this tab and go back to Discord.', 'ok');
    showWallets(res.wallets);
    $('again').hidden = false;
  }

  function showWallets(list) {
    $('wallet-list').replaceChildren(...(list || []).map((w) => {
      const li = document.createElement('li');
      const address = document.createElement('span');
      address.textContent = short(w.address);
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = w.kind === 'evm' ? 'EVM' : 'SOLANA';
      li.append(address, tag);
      return li;
    }));
    $('wallets').hidden = !(list && list.length);
  }

  $('again').addEventListener('click', () => {
    $('connect').hidden = false;
    $('again').hidden = true;
    say('');
    render();
  });

  function showMobile() {
    const here = encodeURIComponent(location.href);
    const ref = encodeURIComponent(location.origin);
    const links = [
      ['MetaMask', 'https://metamask.app.link/dapp/' + location.host + location.pathname + location.search],
      ['Coinbase Wallet', 'https://go.cb-w.com/dapp?cb_url=' + here],
      ['Trust Wallet', 'https://link.trustwallet.com/open_url?coin_id=60&url=' + here],
      ['Phantom', 'https://phantom.app/ul/browse/' + here + '?ref=' + ref],
    ];
    if (flags.solana === '1') links.push(['Solflare', 'https://solflare.com/ul/v1/browse/' + here + '?ref=' + ref]);
    $('mobile-text').textContent = IN_APP
      ? 'This app opened the page in its own window, where no wallet can sign. Open it in your browser '
        + '(menu > Open in browser), or pick your wallet below.'
      : 'On a phone you sign inside your wallet app. Pick your wallet: this page opens in its browser, '
        + 'then press your wallet there.';
    $('mobile-list').replaceChildren(...links.map(([name, url]) => {
      const a = document.createElement('a');
      a.className = 'btn';
      a.href = url;
      a.textContent = 'Open in ' + name;
      return a;
    }));
    $('mobile').hidden = false;
  }

  $('copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      $('copy').textContent = 'Link copied';
    } catch (e) {
      say('Copy failed: copy the link from the address bar instead.', 'bad');
    }
  });

  async function start() {
    try {
      const me = await api('/api/me' + (STATE ? '?state=' + encodeURIComponent(STATE) : ''));
      $('who-name').textContent = me.user.name || me.user.id;
      $('who').hidden = false;
      showWallets(me.wallets);
      $('connect').hidden = false;
      render();
      if (MOBILE && !IN_WALLET) {
        // Wallet providers announce themselves right away; a phone browser without one never will.
        setTimeout(() => { if (!evmChoices().length && !solChoices().length) showMobile(); }, 1500);
      }
    } catch (e) {
      say(e.message, 'bad');
      if (!STATE && flags.oauth === '1') $('login').hidden = false;
    }
  }

  start();
})();
