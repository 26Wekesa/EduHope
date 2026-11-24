(function () {
  const key = 'bold-cookie-preferences-v1';
  const banner = document.getElementById('cookieConsent');
  if (!banner) return;

  const panel = document.getElementById('cookiePreferences');
  const manageButton = document.getElementById('cookieManageButton');
  const analyticsInput = document.getElementById('cookieAnalytics');
  const marketingInput = document.getElementById('cookieMarketing');
  const status = document.getElementById('cookieConsentStatus');

  function readPreferences() {
    try {
      const saved = window.localStorage.getItem(key);
      if (!saved) return null;

      const preferences = JSON.parse(saved);
      if (
        preferences.version === 1
        && typeof preferences.analytics === 'boolean'
        && typeof preferences.marketing === 'boolean'
      ) {
        return preferences;
      }

      status.textContent = 'Your saved cookie choice could not be read. Please choose again.';
    } catch (error) {
      status.textContent = 'Cookie choices could not be read in this browser. Optional trackers remain disabled.';
    }

    return null;
  }

  function savePreferences(analytics, marketing) {
    const preferences = {
      version: 1,
      analytics: analytics,
      marketing: marketing,
      savedAt: new Date().toISOString(),
    };

    try {
      window.localStorage.setItem(key, JSON.stringify(preferences));
    } catch (error) {
      status.textContent = 'Your choice could not be saved. Optional trackers remain disabled in this site.';
      return;
    }

    status.textContent = 'Your cookie choice has been saved. You can change it later using Cookie settings.';
    window.boldCookiePreferences = preferences;
    banner.hidden = true;
    panel.hidden = true;
    manageButton.setAttribute('aria-expanded', 'false');
  }

  function openPreferences() {
    const current = readPreferences();
    analyticsInput.checked = Boolean(current && current.analytics);
    marketingInput.checked = Boolean(current && current.marketing);
    banner.hidden = false;
    panel.hidden = false;
    manageButton.setAttribute('aria-expanded', 'true');
    analyticsInput.focus();
  }

  const existing = readPreferences();
  window.boldCookiePreferences = existing;
  if (!existing) banner.hidden = false;

  banner.querySelectorAll('[data-cookie-choice]').forEach(function (button) {
    button.addEventListener('click', function () {
      const acceptAll = button.dataset.cookieChoice === 'all';
      savePreferences(acceptAll, acceptAll);
    });
  });

  manageButton.addEventListener('click', function () {
    if (panel.hidden) {
      openPreferences();
    } else {
      panel.hidden = true;
      manageButton.setAttribute('aria-expanded', 'false');
    }
  });

  document.getElementById('cookieSavePreferences').addEventListener('click', function () {
    savePreferences(analyticsInput.checked, marketingInput.checked);
  });

  document.getElementById('cookieClosePreferences').addEventListener('click', function () {
    panel.hidden = true;
    manageButton.setAttribute('aria-expanded', 'false');
    manageButton.focus();
  });

  document.querySelectorAll('[data-cookie-settings]').forEach(function (link) {
    link.addEventListener('click', function (event) {
      event.preventDefault();
      openPreferences();
    });
  });
})();
