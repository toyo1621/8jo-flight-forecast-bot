document.addEventListener("DOMContentLoaded", () => {
  const openDialog = (card) => {
    const dialog = document.getElementById(card.dataset.dialog);
    if (dialog && !dialog.open) dialog.showModal();
  };

  document.querySelectorAll(".flight[data-dialog]").forEach((card) => {
    card.addEventListener("click", () => openDialog(card));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDialog(card);
      }
    });
  });

  document.querySelectorAll(".flight-dialog").forEach((dialog) => {
    dialog.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
});
