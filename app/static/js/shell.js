/* SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
 * SPDX-License-Identifier: Apache-2.0
 */

(function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.querySelector("#topnav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
})();
