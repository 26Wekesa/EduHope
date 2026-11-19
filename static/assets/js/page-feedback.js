(function () {
  "use strict";

  const progressBar = document.getElementById("pageProgress");
  const progressStatus = document.getElementById("pageProgressStatus");
  if (!progressBar || !progressStatus) return;

  let showTimer;
  let activeForm;
  let isLoading = false;

  function startLoading(message, form) {
    if (isLoading) return;
    isLoading = true;
    activeForm = form || null;

    showTimer = window.setTimeout(function () {
      document.documentElement.classList.add("is-loading");
      progressBar.setAttribute("aria-valuetext", message);
      progressStatus.textContent = message;
    }, 150);
    if (activeForm) activeForm.setAttribute("aria-busy", "true");
  }

  function resetLoading() {
    window.clearTimeout(showTimer);
    document.documentElement.classList.remove("is-loading");
    progressBar.setAttribute("aria-valuetext", "Loading page");
    progressStatus.textContent = "";
    if (activeForm) activeForm.removeAttribute("aria-busy");
    activeForm = null;
    isLoading = false;
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    if (!(event.target instanceof Element)) return;
    const link = event.target.closest("a[href]");
    if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;

    const destination = new URL(link.href, window.location.href);
    if (destination.origin !== window.location.origin) return;
    if (destination.pathname === window.location.pathname && destination.search === window.location.search) return;

    startLoading("Loading page");
  });

  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement) || form.method.toLowerCase() === "get" || (form.target && form.target !== "_self")) return;

    startLoading("Submitting form", form);
  });

  window.addEventListener("pageshow", resetLoading);
})();
