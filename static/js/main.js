// Close the mobile nav after tapping a link, and on outside click.
document.addEventListener("click", (e) => {
  const link = e.target.closest(".main-nav a");
  if (link) document.body.classList.remove("nav-open");
});

// Subtle shadow on header once scrolled.
const header = document.querySelector(".site-header");
const onScroll = () => header.classList.toggle("scrolled", window.scrollY > 8);
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();
