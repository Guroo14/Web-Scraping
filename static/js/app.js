(function () {
  const root = document.documentElement;
  const toggle = document.querySelector("[data-theme-toggle]");
  const icon = document.querySelector("[data-theme-icon]");
  const storedTheme = localStorage.getItem("karma-theme");
  const preferredDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

  function setTheme(theme) {
    root.setAttribute("data-bs-theme", theme);
    localStorage.setItem("karma-theme", theme);
    if (icon) {
      icon.textContent = theme === "dark" ? "Light" : "Dark";
    }
  }

  setTheme(storedTheme || (preferredDark ? "dark" : "light"));

  if (toggle) {
    toggle.addEventListener("click", function () {
      const nextTheme = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      setTheme(nextTheme);
    });
  }

  document.querySelectorAll("[data-gallery-src]").forEach(function (button) {
    button.addEventListener("click", function () {
      const mainImage = document.getElementById("galleryMain");
      if (!mainImage) {
        return;
      }
      mainImage.src = button.dataset.gallerySrc;
      document.querySelectorAll("[data-gallery-src]").forEach(function (thumb) {
        thumb.classList.remove("active");
      });
      button.classList.add("active");
    });
  });

  document.querySelectorAll(".filter-panel select").forEach(function (select) {
    select.addEventListener("change", function () {
      if (window.innerWidth >= 992) {
        select.form.requestSubmit();
      }
    });
  });
})();
