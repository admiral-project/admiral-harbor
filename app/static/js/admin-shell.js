/* SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
 * SPDX-License-Identifier: Apache-2.0
 */

(function () {
  function toggleSidebar() {
    var shell = document.getElementById("admiral-shell");
    if (!shell) return;
    if (window.innerWidth <= 768) {
      shell.classList.toggle("sidebar-open");
    } else {
      shell.classList.toggle("sidebar-collapsed");
    }
  }

  function toggleSubnav(link) {
    var item = link.closest(".pf-c-nav__item.pf-m-expandable");
    if (!item) return;
    item.classList.toggle("pf-m-expanded");
    var subnav = item.querySelector(".pf-c-nav__subnav");
    if (subnav) {
      subnav.hidden = !subnav.hidden;
    }
  }

  document.addEventListener("click", function (event) {
    var toggleButton = event.target.closest("[data-action='toggle-sidebar']");
    if (toggleButton) {
      event.preventDefault();
      toggleSidebar();
      return;
    }

    var subnavLink = event.target.closest("[data-action='toggle-subnav']");
    if (subnavLink) {
      event.preventDefault();
      toggleSubnav(subnavLink);
      return;
    }

    var shell = document.getElementById("admiral-shell");
    if (!shell) return;
    if (window.innerWidth <= 768 && shell.classList.contains("sidebar-open")) {
      var sidebar = shell.querySelector(".pf-c-page__sidebar");
      if (
        sidebar &&
        !sidebar.contains(event.target) &&
        !event.target.closest(".mobile-menu-button") &&
        !event.target.closest(".mobile-topbar")
      ) {
        shell.classList.remove("sidebar-open");
      }
    }
  });
})();
