"use strict";

const TOP_BUTTON_SCROLL_THRESHOLD_PX = 480;
const MOBILE_LAYOUT_MAX_WIDTH_PX = 900;
const COPY_SUCCESS_DISPLAY_MS = 1600;

const body = document.body;
const sidebar = document.getElementById("sidebar");
const sidebarOverlay = document.getElementById("sidebar-overlay");
const tocToggle = document.getElementById("toc-toggle");
const backToTopButton = document.getElementById("back-to-top");
const tocLinks = Array.from(document.querySelectorAll(".toc-list a"));
const observedSections = Array.from(document.querySelectorAll("main section[id]"));
const copyButtons = Array.from(document.querySelectorAll(".copy-button"));

function isMobileLayout() {
  return window.innerWidth <= MOBILE_LAYOUT_MAX_WIDTH_PX;
}

function setSidebarOpen(isOpen) {
  body.classList.toggle("sidebar-open", isOpen);
  tocToggle.setAttribute("aria-expanded", String(isOpen));
  sidebarOverlay.setAttribute("aria-hidden", String(!isOpen));
}

function closeMobileSidebar() {
  if (isMobileLayout()) {
    setSidebarOpen(false);
  }
}

function updateBackToTopVisibility() {
  backToTopButton.classList.toggle(
    "visible",
    window.scrollY >= TOP_BUTTON_SCROLL_THRESHOLD_PX,
  );
}

function setActiveTocLink(sectionId) {
  tocLinks.forEach((link) => {
    const isActive = link.getAttribute("href") === `#${sectionId}`;
    link.classList.toggle("active", isActive);
    if (isActive) {
      link.setAttribute("aria-current", "location");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

function copyTextWithLegacyFallback(text) {
  const temporaryTextArea = document.createElement("textarea");
  temporaryTextArea.value = text;
  temporaryTextArea.setAttribute("readonly", "");
  temporaryTextArea.style.position = "fixed";
  temporaryTextArea.style.opacity = "0";
  document.body.appendChild(temporaryTextArea);
  temporaryTextArea.select();
  const copied = document.execCommand("copy");
  temporaryTextArea.remove();
  return copied;
}

async function copyCode(button) {
  const code = button.closest(".code-block")?.querySelector("code");
  if (!code) {
    return;
  }

  const originalLabel = button.textContent;
  let copied = false;
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(code.textContent);
      copied = true;
    }
  } catch {
    copied = false;
  }
  if (!copied) {
    copied = copyTextWithLegacyFallback(code.textContent);
  }
  button.textContent = copied
    ? "コピーしました"
    : "コピーできません";

  window.setTimeout(() => {
    button.textContent = originalLabel;
  }, COPY_SUCCESS_DISPLAY_MS);
}

tocToggle.addEventListener("click", () => {
  setSidebarOpen(!body.classList.contains("sidebar-open"));
});

sidebarOverlay.addEventListener("click", () => setSidebarOpen(false));

tocLinks.forEach((link) => {
  link.addEventListener("click", closeMobileSidebar);
});

copyButtons.forEach((button) => {
  button.addEventListener("click", () => copyCode(button));
});

backToTopButton.addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
});

window.addEventListener("scroll", updateBackToTopVisibility, { passive: true });
window.addEventListener("resize", () => {
  if (!isMobileLayout()) {
    setSidebarOpen(false);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && body.classList.contains("sidebar-open")) {
    setSidebarOpen(false);
    tocToggle.focus();
  }
});

const sectionObserver = new IntersectionObserver(
  (entries) => {
    const visibleEntry = entries
      .filter((entry) => entry.isIntersecting)
      .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
    if (visibleEntry) {
      setActiveTocLink(visibleEntry.target.id);
    }
  },
  {
    rootMargin: "-18% 0px -68% 0px",
    threshold: [0.05, 0.2, 0.5],
  },
);

observedSections.forEach((section) => sectionObserver.observe(section));
updateBackToTopVisibility();
setActiveTocLink(observedSections[0]?.id ?? "overview");
