const tocLinks = [...document.querySelectorAll(".toc-list a")];
const observedSections = tocLinks
  .map((link) => document.querySelector(link.hash))
  .filter(Boolean);

if ("IntersectionObserver" in window && observedSections.length) {
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
      if (!visible.length) return;
      const activeId = visible[0].target.id;
      tocLinks.forEach((link) => {
        if (link.hash === `#${activeId}`) link.setAttribute("aria-current", "true");
        else link.removeAttribute("aria-current");
      });
    },
    { rootMargin: "-18% 0px -70%", threshold: 0 },
  );
  observedSections.forEach((section) => observer.observe(section));
}

document.querySelectorAll(".mobile-nav a").forEach((link) => {
  link.addEventListener("click", () => link.closest("details")?.removeAttribute("open"));
});
