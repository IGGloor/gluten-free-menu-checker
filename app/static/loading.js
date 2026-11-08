// Shows the forecast loading overlay while the server reads the menu and asks Gemini.
// The form works without this script; it only makes the wait feel shorter.

// The overlay shows these as "Step N of 5"...
const LOADING_STEPS = [
  "Checking the radar for hidden gluten…",
  "Low-pressure dairy front moving in…",
  "Scanning for stray anchovies…",
  "Asking the broth where it came from…",
  "Plating up your forecast…",
];
// ...and keeps rotating these on the last step if the server is still working.
const WAITING_MESSAGES = [
  "Interrogating the croutons…",
  "Checking the sauces for sneaky wheat…",
  "Asking the cheese some hard questions…",
  "Politely declining the bread basket…",
  "Consulting the chef (in spirit)…",
  "Plating up your forecast…",
];
const LOADING_STEP_MS = 2500;

const form = document.getElementById("menu-form");
const loader = document.getElementById("loader");
const loaderMessage = document.getElementById("loader-message");
const progressFill = document.getElementById("progress-fill");
const progressLabel = document.getElementById("progress-label");

let loaderTimer = null;

form.addEventListener("submit", () => {
  form.querySelector("button[type=submit]").disabled = true;
  loader.hidden = false;
  document.body.classList.add("modal-open");

  let step = 0;
  let waiting = 0;
  showStep(0);

  // Steps 1–5 advance on a timer; once on the last step, keep rotating
  // messages until the results page loads so the overlay never looks stuck.
  loaderTimer = setInterval(() => {
    if (step < LOADING_STEPS.length - 1) {
      step += 1;
      showStep(step);
    } else {
      fadeMessage(WAITING_MESSAGES[waiting % WAITING_MESSAGES.length]);
      waiting += 1;
    }
  }, LOADING_STEP_MS);
});

// Coming back with the browser's Back button can restore the page with the overlay still open.
window.addEventListener("pageshow", () => {
  clearInterval(loaderTimer);
  loader.hidden = true;
  loaderMessage.textContent = "";
  document.body.classList.remove("modal-open");
  form.querySelector("button[type=submit]").disabled = false;
});

function showStep(step) {
  const total = LOADING_STEPS.length;
  fadeMessage(LOADING_STEPS[step]);
  // Hold just short of full until the results arrive
  progressFill.style.width = `${Math.round(((step + 1) / total) * 100) - (step === total - 1 ? 5 : 0)}%`;
  progressLabel.textContent = `Step ${step + 1} of ${total}`;
}

function fadeMessage(text) {
  if (!loaderMessage.textContent) {
    loaderMessage.textContent = text;
    return;
  }
  loaderMessage.classList.add("is-fading");
  setTimeout(() => {
    loaderMessage.textContent = text;
    loaderMessage.classList.remove("is-fading");
  }, 300);
}
