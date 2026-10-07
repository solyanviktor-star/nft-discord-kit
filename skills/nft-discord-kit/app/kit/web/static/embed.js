// Show the open giveaways on any website:
//   <div id="nft-raffles"></div>
//   <script src="https://verify.example.com/embed.js" async></script>
// Optional: data-target="#some-other-element" on the script tag. Entering happens on /raffles.
(() => {
  'use strict';
  const script = document.currentScript;
  if (!script) return;
  const origin = new URL(script.src).origin;
  const target = document.querySelector(script.getAttribute('data-target') || '#nft-raffles');
  if (!target) return;
  fetch(origin + '/api/raffles')
    .then((res) => res.json())
    .then((data) => {
      const open = (data.raffles || []).filter((r) => r.status === 'open');
      target.replaceChildren();
      if (!open.length) {
        target.textContent = 'No giveaways running right now.';
        return;
      }
      const list = document.createElement('ul');
      list.className = 'nft-raffles';
      open.forEach((r) => {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.href = origin + '/raffles#' + encodeURIComponent(r.id);
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = r.title;
        const meta = document.createElement('span');
        meta.textContent = ' · ' + r.chain + ' · ' + r.winners + ' winner' + (r.winners === 1 ? '' : 's')
          + ' · ends ' + new Date(r.ends * 1000).toLocaleString();
        item.append(link, meta);
        list.append(item);
      });
      target.append(list);
    })
    .catch(() => {
      target.textContent = 'Giveaways are unavailable right now.';
    });
})();
