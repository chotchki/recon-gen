// BX.17.a (2026-06-11) — operator-locked quick-pick chip strip for ISO
// 8601 duration FieldSpecs (currently `Rail.max_pending_age` +
// `Rail.max_unbundled_age`; the kind="duration" FieldKind any new
// Duration-typed field opts into).
//
// Wire shape (rendered by `_render_duration_picker_field`):
//
//   <fieldset data-role="duration-picker">
//     <input data-role="duration-free-text" name="<spec.name>" ...>
//     <div data-role="duration-quick-picks">
//       <button data-duration-pick="PT0S" data-target="field-<spec.name>" ...>Instant</button>
//       <button data-duration-pick="PT1H" data-target="field-<spec.name>" ...>1h</button>
//       <button data-duration-pick="PT24H" data-target="field-<spec.name>" ...>EOD</button>
//       <button data-duration-pick="P1D" data-target="field-<spec.name>" ...>Next-day</button>
//     </div>
//   </fieldset>
//
// Two delegated listeners on document.body:
//   - click on `[data-duration-pick]` → fill the free-text input named by
//     `data-target` + flag the clicked chip active + un-flag siblings.
//   - input on `[data-role="duration-free-text"]` → un-flag any active
//     chip whose data-duration-pick value no longer matches.
//
// Minimal-JS posture: no module imports, no per-page bootstrap call,
// no localStorage. The active-on-render state is server-emitted via
// `data-active="true"`; this shim only HANDLES interactions.

(function () {
  const ACTIVE_CLS = (
    "px-2 py-0.5 text-xs rounded-sm border cursor-pointer " +
    "bg-accent text-white border-accent hover:bg-accent/90"
  );
  const INACTIVE_CLS = (
    "px-2 py-0.5 text-xs rounded-sm border cursor-pointer " +
    "bg-white text-primary-fg border-surface-border " +
    "hover:bg-link-tint hover:border-accent"
  );

  function applyActiveState(chip, isActive) {
    if (isActive) {
      chip.setAttribute("data-active", "true");
      chip.className = ACTIVE_CLS;
    } else {
      chip.removeAttribute("data-active");
      chip.className = INACTIVE_CLS;
    }
  }

  function syncChipsForInput(input) {
    // Find the enclosing picker fieldset + its sibling chip group;
    // flag the chip whose value matches the input's current value.
    const fieldset = input.closest('[data-role="duration-picker"]');
    if (!fieldset) return;
    const chips = fieldset.querySelectorAll("[data-duration-pick]");
    const currentVal = input.value;
    chips.forEach((chip) => {
      const chipVal = chip.getAttribute("data-duration-pick");
      applyActiveState(chip, chipVal === currentVal);
    });
  }

  document.addEventListener("click", function (evt) {
    const chip = evt.target.closest("[data-duration-pick]");
    if (!chip) return;
    const targetId = chip.getAttribute("data-target");
    const pickValue = chip.getAttribute("data-duration-pick");
    if (!targetId || pickValue === null) return;
    const input = document.getElementById(targetId);
    if (!input) return;
    input.value = pickValue;
    // Fire `input` so any downstream listeners (form validation,
    // HTMX hx-trigger="input changed") see the synthetic change.
    input.dispatchEvent(new Event("input", { bubbles: true }));
    syncChipsForInput(input);
  });

  document.addEventListener("input", function (evt) {
    const input = evt.target.closest('[data-role="duration-free-text"]');
    if (!input) return;
    syncChipsForInput(input);
  });
})();
