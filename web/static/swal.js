/** SweetAlert2 helpers — toasts top-right, interactions centered. */
const AppSwal = {
  theme: {
    customClass: {
      popup: "app-swal",
      title: "app-swal-title",
      htmlContainer: "app-swal-body",
      actions: "app-swal-actions",
      confirmButton: "btn btn-primary",
      cancelButton: "btn",
      icon: "app-swal-icon",
    },
    buttonsStyling: false,
  },
  interaction: {
    position: "center",
    grow: false,
    width: "24rem",
  },
};

const Toast = Swal.mixin({
  toast: true,
  position: "top-end",
  showConfirmButton: false,
  timer: 3200,
  timerProgressBar: true,
  buttonsStyling: false,
  customClass: {
    popup: "app-swal-toast",
    title: "app-swal-toast-title",
    icon: "app-swal-toast-icon",
  },
});

function toast(message, icon) {
  return Toast.fire({ title: message, icon: icon || undefined });
}

function toastError(message) {
  return toast(message, "error");
}

function toastSuccess(message) {
  return toast(message, "success");
}

async function appConfirm({
  title,
  text,
  html,
  confirmText = "Confirm",
  cancelText = "Cancel",
  icon = "warning",
  danger = confirmText.toLowerCase() === "delete",
}) {
  const result = await Swal.fire({
    title,
    text,
    html,
    icon,
    showCancelButton: true,
    confirmButtonText: confirmText,
    cancelButtonText: cancelText,
    reverseButtons: true,
    focusCancel: true,
    ...AppSwal.theme,
    ...AppSwal.interaction,
    customClass: {
      ...AppSwal.theme.customClass,
      confirmButton: danger ? "btn btn-danger" : "btn btn-primary",
    },
  });
  return result.isConfirmed;
}

async function appAlert({
  title,
  text,
  html,
  icon = "info",
  confirmText = "OK",
}) {
  await Swal.fire({
    title,
    text,
    html,
    icon,
    confirmButtonText: confirmText,
    ...AppSwal.theme,
    ...AppSwal.interaction,
  });
}

async function showClampAlert(clamped, requestedStart) {
  const items = clamped
    .map(({ sym, earliest }) => `<li><strong>${sym}</strong> → from ${earliest}</li>`)
    .join("");
  await appAlert({
    title: "Start date clamped",
    html: `<p>Your start date <strong>${requestedStart}</strong> is before data exists for:</p>
      <ul class="clamp-list">${items}</ul>
      <p class="swal-muted">Each symbol downloads from its own earliest date.</p>`,
    icon: "info",
    confirmText: "Got it",
  });
}
