/* SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
 * SPDX-License-Identifier: Apache-2.0
 */

(function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute("content") : "";
  if (!token) {
    return;
  }

  function addToken(form) {
    if (!form) return;
    var method = (form.getAttribute("method") || "get").toUpperCase();
    if (method === "GET" || form.dataset.csrf === "false") return;
    if (form.querySelector('input[name="csrf_token"]')) return;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrf_token";
    input.value = token;
    form.appendChild(input);
  }

  document.querySelectorAll("form").forEach(addToken);
  document.addEventListener(
    "submit",
    function (event) {
      addToken(event.target);
    },
    true
  );
})();
