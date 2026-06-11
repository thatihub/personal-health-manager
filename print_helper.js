document.addEventListener("DOMContentLoaded", () => {
  // Prevent duplicate print buttons
  if (document.querySelector(".print-btn")) return;

  const btn = document.createElement("button");
  btn.className = "print-btn";
  btn.type = "button";
  btn.setAttribute("aria-label", "Print this report");
  btn.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: inline-block; vertical-align: middle;">
      <polyline points="6 9 6 2 18 2 18 9"></polyline>
      <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
      <rect x="6" y="14" width="12" height="8"></rect>
    </svg>
    <span style="vertical-align: middle; margin-left: 6px;">Print Report</span>
  `;
  
  btn.onclick = () => {
    window.print();
  };
  
  document.body.appendChild(btn);
});
