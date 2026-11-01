// Desktop sidebar: keep the active item in sync after every render, «Назад» included.
// setView() highlights the item it was called with, but goBack() and the Mini App modules
// switch state.view directly; detail pages belong to the section they were opened from.
(function () {
  const SECTION_OF = { lead: 'leads', telegram: 'leads' };
  const baseRender = window.render;
  window.render = async function () {
    const result = await baseRender.apply(this, arguments);
    const active = SECTION_OF[state.view] || state.view;
    document.querySelectorAll('.sidebar nav button[data-view]')
      .forEach((button) => button.classList.toggle('active', button.dataset.view === active));
    return result;
  };
  render = window.render;
})();
