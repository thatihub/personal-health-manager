document.addEventListener("DOMContentLoaded", () => {
  // Prevent duplicate print buttons
  if (document.querySelector(".print-nav-link")) return;

  const nav = document.querySelector(".nav") || document.querySelector(".top-nav");
  if (!nav) return;

  const link = document.createElement("a");
  link.href = "#";
  link.className = "print-nav-link";
  link.setAttribute("aria-label", "Print this report");
  link.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: inline-block; vertical-align: middle;">
      <polyline points="6 9 6 2 18 2 18 9"></polyline>
      <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
      <rect x="6" y="14" width="12" height="8"></rect>
    </svg>
    <span style="vertical-align: middle; margin-left: 5px;">Print</span>
  `;
  
  link.onclick = (e) => {
    e.preventDefault();
    window.print();
  };
  
  nav.appendChild(link);
});
