// Promise-based yes/no dialog. Resolves true on confirm, false on cancel,
// backdrop click, or Escape. Enter confirms. Replaces window.confirm() so
// destructive actions (delete chat, delete CSV) match the app's styling.
//
// Usage:
//   if (!(await confirmDialog({ title: "Delete?", message: "...", destructive: true }))) return;

function confirmDialog({
  title = "Are you sure?",
  message = "",
  confirmText = "Confirm",
  cancelText = "Cancel",
  destructive = false,
} = {}) {
  return new Promise((resolve) => {
    const backdrop = document.getElementById("confirmBackdrop");
    const modal = document.getElementById("confirmModal");
    const okBtn = document.getElementById("confirmOkBtn");
    const cancelBtn = document.getElementById("confirmCancelBtn");
    document.getElementById("confirmTitle").textContent = title;
    document.getElementById("confirmMessage").textContent = message;
    okBtn.textContent = confirmText;
    cancelBtn.textContent = cancelText;
    okBtn.classList.toggle("destructive", !!destructive);

    function close(result) {
      backdrop.classList.remove("open");
      modal.classList.remove("open");
      okBtn.removeEventListener("click", onConfirm);
      cancelBtn.removeEventListener("click", onCancel);
      backdrop.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKey);
      resolve(result);
    }
    const onConfirm = () => close(true);
    const onCancel = () => close(false);
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); onCancel(); }
      else if (event.key === "Enter") { event.preventDefault(); onConfirm(); }
    };

    okBtn.addEventListener("click", onConfirm);
    cancelBtn.addEventListener("click", onCancel);
    backdrop.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKey);

    backdrop.classList.add("open");
    modal.classList.add("open");
    // Defer focus so the opening animation doesn't fight it.
    requestAnimationFrame(() => okBtn.focus());
  });
}
