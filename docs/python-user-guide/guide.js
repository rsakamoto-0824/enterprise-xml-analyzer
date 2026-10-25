"use strict";
const imageViewer = document.getElementById("image-viewer");
const viewerImage = document.getElementById("viewer-image");
let previousImageButton;
document.querySelectorAll(".shot-zoom").forEach((button) => {
  button.addEventListener("click", () => {
    const image = button.querySelector("img");
    previousImageButton = button;
    viewerImage.src = image.currentSrc || image.src;
    viewerImage.alt = image.alt;
    imageViewer.showModal();
    document.getElementById("close-viewer").focus();
  });
});
document.getElementById("close-viewer").addEventListener("click", () => imageViewer.close());
imageViewer.addEventListener("close", () => previousImageButton?.focus());
