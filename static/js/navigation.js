(function () {
  "use strict";

  function ready(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback);
    } else {
      callback();
    }
  }

  ready(function () {
    var toggle = document.querySelector(".mobile-menu-toggle");
    var mobileMenu = document.getElementById("mobile-menu");

    if (toggle && mobileMenu) {
      toggle.addEventListener("click", function () {
        var expanded = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!expanded));
        mobileMenu.hidden = expanded;
        document.body.classList.toggle("menu-open", !expanded);
      });
    }

    document.querySelectorAll(".dropdown-toggle").forEach(function (button) {
      button.addEventListener("click", function () {
        var expanded = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!expanded));
        var menu = button.parentElement.querySelector(".dropdown-menu");
        if (menu) {
          menu.classList.toggle("is-open", !expanded);
        }
      });
    });

    document.querySelectorAll(".thumbnail-row img").forEach(function (thumbnail) {
      thumbnail.addEventListener("click", function () {
        var main = document.querySelector(".product-main-image");
        if (main && thumbnail.src) {
          main.src = thumbnail.src;
        }
      });
    });
  });
}());
