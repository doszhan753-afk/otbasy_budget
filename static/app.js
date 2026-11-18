// Отбасы бюджеті — шағын көмекші скрипт (кітапханасыз).
(function () {
  "use strict";

  // Мобильді мәзір
  var toggle = document.querySelector("[data-nav-toggle]");
  var sidebar = document.getElementById("sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      var open = sidebar.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // Жою алдында растау
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute && e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      e.preventDefault();
    }
  });

  // Ай өзгергенде форманы автоматты жіберу
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () {
      if (el.value && el.form) el.form.submit();
    });
  });

  // Басып шығару
  document.querySelectorAll("[data-print]").forEach(function (btn) {
    btn.addEventListener("click", function () { window.print(); });
  });

  // Операция формасы: таңдалған түрге сай санаттарды ғана көрсету
  var select = document.querySelector("[data-category-select]");
  var radios = document.querySelectorAll("[data-kind-radio]");
  if (select && radios.length) {
    var all = Array.prototype.map.call(select.options, function (o) {
      return { value: o.value, text: o.textContent, kind: o.getAttribute("data-kind") };
    });
    var selected = select.getAttribute("data-selected") || select.value;

    var render = function () {
      var checked = document.querySelector("[data-kind-radio]:checked");
      var kind = checked ? checked.value : "expense";
      var current = select.value || selected;
      select.innerHTML = "";
      all.forEach(function (c) {
        if (c.kind !== kind) return;
        var opt = document.createElement("option");
        opt.value = c.value;
        opt.textContent = c.text;
        opt.setAttribute("data-kind", c.kind);
        if (c.value === current) opt.selected = true;
        select.appendChild(opt);
      });
    };
    radios.forEach(function (r) { r.addEventListener("change", function () { selected = ""; render(); }); });
    render();
  }
})();
