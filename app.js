(() => {
  const qs = (s, r = document) => r.querySelector(s);
  const qsa = (s, r = document) => [...r.querySelectorAll(s)];
  const tabs = qsa('.tab');
  const pages = qsa('.page');
  const reportSelect = qs('#reportType');
  if (reportSelect && !qs('[data-report-select]')) {
    const wrapper = document.createElement('div');
    wrapper.className = 'custom-select';
    wrapper.dataset.reportSelect = '';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'custom-select__trigger';
    trigger.id = 'reportTypeTrigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', 'reportTypeMenu');
    const reportLabel = qs('label[for="reportType"]');
    if (reportLabel) {
      reportLabel.id ||= 'reportTypeLabel';
      reportLabel.htmlFor = trigger.id;
      trigger.setAttribute('aria-labelledby', reportLabel.id);
    }
    const menu = document.createElement('div');
    menu.className = 'custom-select__menu';
    menu.id = 'reportTypeMenu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;
    [...reportSelect.options].forEach((option, index) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'custom-select__option';
      item.setAttribute('role', 'option');
      item.dataset.value = option.value;
      item.dataset.index = String(index);
      item.textContent = option.textContent;
      item.addEventListener('click', () => choose(option.value));
      menu.append(item);
    });
    reportSelect.classList.add('report-select--native');
    reportSelect.tabIndex = -1;
    reportSelect.after(wrapper);
    wrapper.append(trigger, menu);
    const options = () => qsa('.custom-select__option', menu);
    const sync = () => {
      const selected = reportSelect.options[reportSelect.selectedIndex];
      trigger.innerHTML = `<span>${selected.textContent}</span><span class="custom-select__chevron" aria-hidden="true">⌄</span>`;
      options().forEach(item => item.setAttribute('aria-selected', String(item.dataset.value === reportSelect.value)));
    };
    const close = (restoreFocus = false) => {
      trigger.setAttribute('aria-expanded', 'false');
      menu.classList.remove('is-open');
      window.setTimeout(() => { if (trigger.getAttribute('aria-expanded') === 'false') menu.hidden = true; }, 180);
      if (restoreFocus) trigger.focus();
    };
    const open = (focusSelected = false) => {
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      requestAnimationFrame(() => menu.classList.add('is-open'));
      if (focusSelected) options()[reportSelect.selectedIndex]?.focus();
    };
    const choose = value => {
      reportSelect.value = value;
      reportSelect.dispatchEvent(new Event('change', { bubbles: true }));
      sync();
      close(true);
    };
    trigger.addEventListener('click', () => trigger.getAttribute('aria-expanded') === 'true' ? close() : open());
    trigger.addEventListener('keydown', event => {
      if (['Enter', ' ', 'ArrowDown'].includes(event.key)) { event.preventDefault(); open(true); }
      if (event.key === 'Escape') close();
    });
    menu.addEventListener('keydown', event => {
      const items = options();
      const current = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') { event.preventDefault(); items[(current + 1) % items.length]?.focus(); }
      if (event.key === 'ArrowUp') { event.preventDefault(); items[(current - 1 + items.length) % items.length]?.focus(); }
      if (event.key === 'Home') { event.preventDefault(); items[0]?.focus(); }
      if (event.key === 'End') { event.preventDefault(); items.at(-1)?.focus(); }
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); document.activeElement?.click(); }
      if (event.key === 'Escape') { event.preventDefault(); close(true); }
    });
    document.addEventListener('click', event => { if (!wrapper.contains(event.target)) close(); });
    reportSelect.addEventListener('change', sync);
    sync();
  }
  tabs.forEach(tab => tab.addEventListener('click', () => {
    tabs.forEach(t => t.setAttribute('aria-selected', String(t === tab)));
    pages.forEach(page => { page.hidden = page.id !== tab.getAttribute('aria-controls'); });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }));
  qsa('[data-tab-target]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    qs(`#${link.dataset.tabTarget}`)?.click();
  }));
  const depart = qs('#departInput');
  depart?.addEventListener('change', () => { qs('#customTimeField')?.toggleAttribute('hidden', depart.value !== 'custom'); });
  qsa('.seg button').forEach(btn => btn.addEventListener('click', () => {
    qsa('.seg button').forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
  }));
  qs('#heroPlanBtn')?.addEventListener('click', () => {
    const routeCard = qs('#fromInput')?.closest('.card');
    routeCard?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => qs('#fromInput')?.focus(), 350);
  });
  qs('#heroExploreBtn')?.addEventListener('click', () => {
    qs('.grid--route')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  const heroVisual = qs('.hero__visual');
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (heroVisual && !reduceMotion) {
    let frame = 0;
    heroVisual.addEventListener('pointermove', event => {
      cancelAnimationFrame(frame);
      const rect = heroVisual.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width - 0.5) * 2;
      const y = ((event.clientY - rect.top) / rect.height - 0.5) * 2;
      frame = requestAnimationFrame(() => heroVisual.style.setProperty('--parallax', `${x * 3}px, ${y * 3}px`));
    }, { passive: true });
    heroVisual.addEventListener('pointerleave', () => {
      cancelAnimationFrame(frame);
      heroVisual.style.setProperty('--parallax', '0px, 0px');
    }, { passive: true });
  }
  const plan = qs('#planBtn');
  plan?.addEventListener('click', () => {
    const from = qs('#fromInput')?.value || 'Your location';
    const to = qs('#toInput')?.value || 'Mysuru destination';
    const cards = qs('#routeCards');
    if (cards) cards.innerHTML = `
      <article class="route-card"><strong>Safest route</strong><div class="card__sub">${from} → ${to}</div><div style="color:var(--safe);margin-top:8px">Low reported risk · 24 min</div></article>
      <article class="route-card"><strong>Balanced route</strong><div class="card__sub">Well-lit streets with a small time trade-off</div><div style="color:var(--amber);margin-top:8px">Medium reported risk · 19 min</div></article>
      <article class="route-card"><strong>Fastest route</strong><div class="card__sub">Direct path with fewer route signals</div><div style="color:var(--info);margin-top:8px">Unrated stretches · 15 min</div></article>`;
    const summary = qs('#routeSummary'); if (summary) summary.textContent = `Three estimates for ${from} to ${to}. Compare the trade-off between time and reported risk.`;
    const count = qs('#candidateCount'); if (count) count.textContent = '3 estimates';
    qs('#tab-route')?.click();
  });
  qs('#reportSubmitBtn')?.addEventListener('click', () => {
    const out = qs('#reportResult'); if (out) out.innerHTML = '<div class="status-pill" style="color:var(--safe);border-color:rgba(97,231,207,.35)">Report saved as a community signal.</div>';
  });
  qsa('.panic__btn').forEach(button => {
    let timer;
    const start = () => { timer = setTimeout(() => { const target = button.id === 'panicBtn' ? qs('#panicResult') : qs('#navPanicResult'); if (target) target.innerHTML = '<div class="status-pill" style="color:var(--safe)">Alert prepared for your trusted circle.</div>'; }, 2000); };
    const stop = () => clearTimeout(timer);
    button.addEventListener('pointerdown', start); ['pointerup','pointerleave','pointercancel'].forEach(e => button.addEventListener(e, stop));
  });
  qsa('input[type=range]').forEach(input => input.addEventListener('input', () => input.style.setProperty('--value', `${input.value}%`)));
})();
